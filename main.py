import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List

async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Validate API key from request headers. Bypasses if ADMIN_API_KEY environment variable is not configured."""
    admin_key = os.getenv("ADMIN_API_KEY", "").strip()
    if not admin_key:
        return
    if x_api_key != admin_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
import os
import logging
from logging.handlers import RotatingFileHandler
import time
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Ensure data directory exists for log storage
os.makedirs("data", exist_ok=True)

# Set up logging with rotating file handler
log_handler = RotatingFileHandler("data/server.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        log_handler,
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("acr-ac-rag")

from fhir_converter import convert_text_to_fhir_bundle, extract_scenario_from_bundle, fallback_text_to_fhir_bundle, is_generic_query
from rag_engine import query_acr_guidelines, init_rag, add_clinician_override, get_clinician_overrides, sync_cache_to_gcs
import hashlib
import uuid
from datetime import datetime

# Priority 3: Eager model loading at startup
# Eliminates the 30-40s cold-start penalty on first request
rag_initialized = False
rag_error = None

import hmac

AUDIT_LOG_SALT = os.getenv("AUDIT_LOG_SALT", "default_acr_audit_salt_2026").encode()

# Internal Audit Token and Review Queue Database helpers
def generate_audit_token(session_data: dict) -> str:
    """Generate a tamper-evident Internal Audit Token using HMAC-SHA256. Format: AUDIT-[DATE]-[HMAC]"""
    content = f"{session_data.get('timestamp','')}{session_data.get('scenario','')}{session_data.get('recommendation','')}"
    h = hmac.new(AUDIT_LOG_SALT, content.encode(), hashlib.sha256)
    content_hash = h.hexdigest()[:12].upper()
    date_str = datetime.now().strftime("%Y%m%d")
    return f"AUDIT-{date_str}-{content_hash}"


def get_audit_log_path() -> str:
    """Resolve the active audit log path. Saves directly to GCS mount if configured."""
    gcs_cache_path = os.getenv("CACHE_SOURCE_PATH", "").strip()
    if gcs_cache_path:
        gcs_dir = os.path.dirname(gcs_cache_path)
        return os.path.join(gcs_dir, "logs", "audit_transaction_log.jsonl")
    return "data/logs/audit_transaction_log.jsonl"


def log_audit_transaction(scenario: str, recommendation: str, sources: list, confidence: float = 0.0, abstained: bool = False) -> str:
    """Append Audit Token record to immutable JSONL audit log."""
    import json as json_mod
    log_path = get_audit_log_path()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    timestamp = datetime.now().isoformat()
    session_data = {"timestamp": timestamp, "scenario": scenario, "recommendation": recommendation or ""}
    audit_token = generate_audit_token(session_data)
    record = {
        "audit_token": audit_token,
        "timestamp": timestamp,
        "scenario_hash": hashlib.sha256(scenario.encode()).hexdigest()[:16],
        "recommendation_summary": (recommendation or "")[:500],
        "source_count": len(sources),
        "confidence_score": confidence,
        "abstained": abstained,
        "api_version": "3.0.0",
    }
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json_mod.dumps(record) + "\n")
        logger.info(f"[AUDIT] Logged transaction {audit_token}")
    except Exception as e:
        logger.error(f"[AUDIT] Failed to log transaction: {e}")
    return audit_token


def init_review_queue_db():
    """Ensure the manual_review_queue table exists in SQLite database."""
    import sqlite3
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect("data/query_cache.db", timeout=30.0)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                scenario_text TEXT NOT NULL,
                confidence_score REAL,
                abstention_reason TEXT,
                status TEXT DEFAULT 'pending',
                reviewer_id TEXT,
                reviewed_at TEXT,
                final_recommendation TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


rag_init_lock = asyncio.Lock()

async def ensure_rag_ready():
    global rag_initialized, rag_error
    if not rag_initialized:
        async with rag_init_lock:
            if not rag_initialized:
                logger.info("Initializing RAG synchronously on demand...")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, init_rag)
                rag_initialized = True
                init_review_queue_db()
            
    if rag_error:
        logger.error(f"RAG engine init error detail: {rag_error}")
        raise HTTPException(
            status_code=503,
            detail="RAG engine failed to initialize. Check server logs for details."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load ML models synchronously during startup to utilize Cloud Run startup CPU boost."""
    global rag_initialized, rag_error
    logger.info("[STARTUP] Pre-loading RAG models (embeddings, reranker, LLM)...")
    
    # Diagnostics check on assets existence
    if not os.path.exists("chroma_db_gemini") and not os.path.exists("chroma_db_local"):
         logger.warning("[STARTUP WARNING] Vector database directory not found in current workspace. Ensure ingest.py has been run.")
         
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, init_rag)
        rag_initialized = True
        init_review_queue_db()
        logger.info("[READY] All models loaded — server ready for requests")
    except Exception as e:
        rag_error = str(e)
        logger.critical(f"[CRITICAL STARTUP ERROR] RAG model initialization failed: {e}", exc_info=True)
        
    yield
    # Shutdown: nothing to clean up


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="ACR-AC-RAG Headless API",
    description="FHIR and CDS Hooks compliant backend for ACR guidelines retrieval and protocoling assistance.",
    version="3.0.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all handler to prevent stack trace leakage in responses."""
    logger.error(f"Unhandled exception on {request.method} {request.url.path}", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Please try again later."}
    )

# CORS origins setup
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "")
if allowed_origins_env:
    allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
    )
else:
    # Restrict to standard local development origins for safety when not configured
    default_dev_origins = [
        "http://localhost:8080",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173"
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=default_dev_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
    )

@app.middleware("http")
async def add_request_id_and_latency_tracking(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    start_time = time.perf_counter()
    
    # Store request id in state
    request.state.request_id = request_id
    response = await call_next(request)
    
    latency = time.perf_counter() - start_time
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = f"{latency:.4f}s"
    logger.info(f"[{request_id}] {request.method} {request.url.path} completed in {latency:.4f}s with status {response.status_code}")
    return response

from fastapi.responses import JSONResponse

@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > 5 * 1024 * 1024:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request payload too large. Max allowed size is 5MB."}
                )
        except ValueError:
            pass
    return await call_next(request)

class AnalyzeRequest(BaseModel):
    text: Optional[str] = Field(None, max_length=4000)
    bundle: Optional[Dict[str, Any]] = None

class ProtocolRequest(BaseModel):
    text: Optional[str] = Field(None, max_length=4000)
    bundle: Optional[Dict[str, Any]] = None
    institution_id: Optional[str] = None   # Override default institution

class OverrideRequest(BaseModel):
    query_key: str = Field(..., max_length=4000)
    original_recommendation: str = Field(..., max_length=4000)
    overridden_recommendation: str = Field(..., max_length=4000)
    override_reason: str = Field(..., max_length=4000)
    clinician_notes: str = Field("", max_length=4000)

class AcceptMappingRequest(BaseModel):
    institution_id: str = Field(..., max_length=100)
    acr_scenario_text: str = Field(..., max_length=4000)
    acr_procedure_text: str = Field(..., max_length=4000)
    imaging_protocol_id: Optional[str] = Field(None, max_length=100)
    ir_protocol_id: Optional[str] = Field(None, max_length=100)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    mapping_method: str = Field(..., max_length=100)
    accepted_by: str = Field(..., max_length=100)

class ClaimReviewRequest(BaseModel):
    session_id: str = Field(..., max_length=100)
    reviewer_id: str = Field(..., max_length=100)

class ResolveReviewRequest(BaseModel):
    session_id: str = Field(..., max_length=100)
    reviewer_id: str = Field(..., max_length=100)
    final_recommendation: str = Field(..., max_length=4000)



@app.get("/health")
async def health_check():
    """Lightweight endpoint for frontend health checking."""
    global rag_initialized, rag_error
    if rag_error:
        return {
            "status": "degraded",
            "error": rag_error,
            "message": "RAG engine failed to initialize on startup. Check backend API keys and file mounts."
        }
    elif not rag_initialized:
        return {
            "status": "initializing",
            "message": "RAG engine is still initializing in the background."
        }
    return {"status": "healthy"}


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serve the premium clinical decision support hub dashboard."""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>Frontend index.html not found</h1>", status_code=404)

@app.get("/apple-touch-icon.png")
async def get_apple_touch_icon():
    icon_path = os.path.join(os.path.dirname(__file__), "apple-touch-icon.png")
    if os.path.exists(icon_path):
        return FileResponse(icon_path)
    return HTMLResponse(content="Icon not found", status_code=404)

@app.get("/logo.png")
async def get_logo():
    logo_path = os.path.join(os.path.dirname(__file__), "logo.png")
    if os.path.exists(logo_path):
        return FileResponse(logo_path)
    return HTMLResponse(content="Logo not found", status_code=404)

@app.get("/manifest.json")
async def get_manifest():
    return {
        "name": "ACR-AC RAG Clinical Hub",
        "short_name": "ACR-AC Hub",
        "description": "Appropriateness Criteria & Intelligent Radiology Protocoling Hub",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f8fafc",
        "theme_color": "#0f172a",
        "icons": [
            {
                "src": "/apple-touch-icon.png",
                "sizes": "180x180",
                "type": "image/png"
            },
            {
                "src": "data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🧠</text></svg>",
                "sizes": "192x192 512x512",
                "type": "image/svg+xml"
            }
        ]
    }



@app.get("/v1/guidelines/manifest")
async def get_guidelines_manifest():
    """Retrieve the manifest of ingested guidelines."""
    import json as json_mod
    manifest_path = "data/guideline_manifest.json"
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json_mod.load(f)
        except Exception as e:
            logger.error(f"Failed to read manifest file: {e}")
            raise HTTPException(status_code=500, detail="Failed to read manifest file.")
    return {
        "version": "ACR-AC-DB-LOCAL",
        "release_date": datetime.now().strftime("%Y-%m-%d"),
        "total_pdfs": 0,
        "total_vectors": 0,
        "message": "Manifest not found. Run ingest.py to generate."
    }



AMBIGUITY_THRESHOLD = 0.55

def _route_to_manual_review(scenario_text: str, confidence: float):
    """Insert a case into the manual review queue."""
    import sqlite3
    conn = sqlite3.connect("data/query_cache.db", timeout=30.0)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO manual_review_queue (session_id, scenario_text, confidence_score, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (str(uuid.uuid4()), scenario_text[:2000], confidence, datetime.now().isoformat())
        )
        conn.commit()
        sync_cache_to_gcs()
    except Exception as e:
        logger.error(f"Failed to route to manual review: {e}")
    finally:
        conn.close()

@app.post("/v1/analyze")
@limiter.limit("30/minute")
async def analyze_scenario(request: Request, req: AnalyzeRequest):
    """Analyzes a clinical scenario with abstention gate, confidence routing, and audit token logging."""
    logger.info("Received request on /v1/analyze")
    await ensure_rag_ready()
    if not req.text and not req.bundle:
        logger.warning("AnalyzeRequest missing both 'text' and 'bundle'")
        raise HTTPException(status_code=400, detail="Must provide either 'text' or 'bundle'.")

    if req.text:
        try:
            bundle_obj = convert_text_to_fhir_bundle(req.text)
            bundle_dict = bundle_obj.model_dump()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("Error in Text-to-FHIR conversion", exc_info=True)
            raise HTTPException(status_code=500, detail="Error converting text to clinical resources.")
    else:
        bundle_dict = req.bundle
        try:
            from fhir.resources.bundle import Bundle
            import importlib
            # 1. Validate overall bundle schema
            Bundle.parse_obj(bundle_dict)
            # 2. Deeply validate individual resources using raw dicts
            if isinstance(bundle_dict, dict) and bundle_dict.get("entry"):
                for entry in bundle_dict["entry"]:
                    if isinstance(entry, dict) and entry.get("resource"):
                        res_dict = entry["resource"]
                        if isinstance(res_dict, dict):
                            rtype = res_dict.get("resourceType")
                            if rtype:
                                try:
                                    module = importlib.import_module(f"fhir.resources.{rtype.lower()}")
                                    resource_cls = getattr(module, rtype)
                                    resource_cls.parse_obj(res_dict)
                                except (ImportError, AttributeError):
                                    raise ValueError(f"Unknown FHIR resourceType: {rtype}")
                                except Exception as parse_err:
                                    raise ValueError(f"Invalid {rtype} resource: {parse_err}")
        except Exception as e:
            logger.warning(f"Invalid FHIR Bundle in analyze request: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid FHIR Bundle schema: {str(e)}")

    scenario_str = extract_scenario_from_bundle(bundle_dict)
    rag_query = req.text if req.text else scenario_str

    try:
        result = await asyncio.to_thread(query_acr_guidelines, rag_query, bundle_dict)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error in RAG retrieval", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving ACR appropriateness guidelines.")

    # Abstention gate
    if result.get("abstained"):
        audit_token = log_audit_transaction(scenario_str, None, [], abstained=True)
        logger.info(f"Abstained on scenario — Token: {audit_token}")
        return {
            "status": "abstained",
            "audit_token": audit_token,
            "abstained": True,
            "abstention_reason": result.get("abstention_reason", ""),
            "extracted_scenario": scenario_str,
            "recommendation": None,
            "sources": [],
            "confidence_score": 0.0,
        }

    confidence = result.get("confidence_score", 1.0)

    # Ambiguity routing
    if confidence < AMBIGUITY_THRESHOLD:
        audit_token = log_audit_transaction(scenario_str, result.get("recommendation"), result.get("sources", []), confidence=confidence)
        _route_to_manual_review(scenario_str, confidence)
        logger.info(f"Low-confidence case routed to review — Token: {audit_token}")
        return {
            "status": "routed_to_review",
            "audit_token": audit_token,
            "abstained": False,
            "confidence_score": confidence,
            "extracted_scenario": scenario_str,
            "recommendation": result["recommendation"],
            "sources": result["sources"],
            "review_note": "This case has been flagged as potentially complex or ambiguous. A senior radiologist review has been requested.",
        }

    audit_token = log_audit_transaction(scenario_str, result["recommendation"], result.get("sources", []), confidence=confidence)
    logger.info(f"Successfully analyzed clinical scenario — Token: {audit_token}")
    return {
        "status": "success",
        "audit_token": audit_token,
        "abstained": False,
        "confidence_score": confidence,
        "mock_bundle_used": bundle_dict if req.text else None,
        "extracted_scenario": scenario_str,
        "recommendation": result["recommendation"],
        "sources": result["sources"],
    }


@app.post("/v1/protocol")
@limiter.limit("30/minute")
async def get_draft_protocol_endpoint(request: Request, req: ProtocolRequest):
    """
    The Protocoling Assistant endpoint.
    
    Full pipeline: Clinical Text → FHIR Bundle → ACR RAG → Protocol Mapper → Draft Protocol
    
    Returns:
      - ACR recommendation (what to order)
      - Draft Protocol (how to perform it at this hospital)
      - Safety flags (eGFR, allergies, pregnancy, med holds)
      - Confidence score and evidence provenance
    """
    logger.info("Received request on /v1/protocol")
    await ensure_rag_ready()
    if not req.text and not req.bundle:
        logger.warning("ProtocolRequest missing both 'text' and 'bundle'")
        raise HTTPException(status_code=400, detail="Must provide either 'text' or 'bundle'.")

    # 1. Convert to FHIR Bundle
    if req.text:
        try:
            if is_generic_query(req.text):
                logger.info(f"Generic query detected: '{req.text}'. Bypassing LLM FHIR extraction.")
                bundle_obj = fallback_text_to_fhir_bundle(req.text)
            else:
                bundle_obj = convert_text_to_fhir_bundle(req.text)
            bundle_dict = bundle_obj.model_dump()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("Error in Text-to-FHIR conversion", exc_info=True)
            raise HTTPException(status_code=500, detail="Error converting text to clinical resources.")
    else:
        bundle_dict = req.bundle
        try:
            from fhir.resources.bundle import Bundle
            import importlib
            # 1. Validate overall bundle schema
            Bundle.parse_obj(bundle_dict)
            # 2. Deeply validate individual resources using raw dicts
            if isinstance(bundle_dict, dict) and bundle_dict.get("entry"):
                for entry in bundle_dict["entry"]:
                    if isinstance(entry, dict) and entry.get("resource"):
                        res_dict = entry["resource"]
                        if isinstance(res_dict, dict):
                            rtype = res_dict.get("resourceType")
                            if rtype:
                                try:
                                    module = importlib.import_module(f"fhir.resources.{rtype.lower()}")
                                    resource_cls = getattr(module, rtype)
                                    resource_cls.parse_obj(res_dict)
                                except (ImportError, AttributeError):
                                    raise ValueError(f"Unknown FHIR resourceType: {rtype}")
                                except Exception as parse_err:
                                    raise ValueError(f"Invalid {rtype} resource: {parse_err}")
        except Exception as e:
            logger.warning(f"Invalid FHIR Bundle in protocol request: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid FHIR Bundle schema: {str(e)}")

    # 2. Extract scenario for RAG
    scenario_str = extract_scenario_from_bundle(bundle_dict)

    # 3. Run ACR RAG Engine
    # Use the original raw text query if available to preserve clinical intent (e.g. "how to fix")
    rag_query = req.text if req.text else scenario_str
    try:
        acr_result = await asyncio.to_thread(query_acr_guidelines, rag_query, bundle_dict)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error in RAG retrieval", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving ACR appropriateness guidelines.")

    # 4. Run Protocol Mapper (Service C)
    try:
        from protocol_mapper import get_draft_protocol
        draft = await asyncio.to_thread(
            get_draft_protocol,
            acr_result,
            bundle_dict,
            req.institution_id,
        )
    except Exception as e:
        logger.error("Protocol mapping failed — falling back to partial success", exc_info=True)
        # Protocol mapping is additive — return ACR result even if mapping fails
        audit_token = log_audit_transaction(scenario_str, acr_result.get("recommendation"), acr_result.get("sources", []), confidence=acr_result.get("confidence_score", 1.0))
        return {
            "status": "partial_success",
            "audit_token": audit_token,
            "extracted_scenario": scenario_str,
            "fhir_bundle": bundle_dict,
            "acr_recommendation": acr_result["recommendation"],
            "acr_sources": acr_result["sources"],
            "raw_query": acr_result.get("raw_query"),
            "expanded_query": acr_result.get("expanded_query"),
            "draft_protocol": None,
            "protocol_error": str(e),
        }

    # Closed-loop: if hard contraindications found, re-query for alternatives (skip for generic queries)
    if draft.safety_profile and draft.status in ("matched", "fuzzy_matched") and not (req.text and is_generic_query(req.text)):
        from safety_engine import get_hard_contraindication_triggers, SafetyProfile as SP
        try:
            # Reconstruct SafetyProfile from dict to use the helper
            sp = SP(data_source=draft.safety_profile.get("data_source", "synthetic"))
            from safety_engine import SafetyFlag, LabCheckResult
            sp.safety_flags = [SafetyFlag(**f) for f in draft.safety_profile.get("safety_flags", [])]
            sp.lab_checks = [LabCheckResult(**lc) for lc in draft.safety_profile.get("lab_checks", [])]
            hard_triggers = get_hard_contraindication_triggers(sp)
            if hard_triggers:
                contraindications = [t["message"] for t in hard_triggers]
                alt_query = f"{scenario_str} CONSTRAINT: The following are contraindicated: {'; '.join(contraindications)}. Recommend only non-contrast or alternative modality options."
                logger.info(f"[SAFETY LOOP] Hard contraindication detected — re-querying for alternatives")
                try:
                    alt_result = await asyncio.to_thread(query_acr_guidelines, alt_query, bundle_dict)
                    audit_token = log_audit_transaction(scenario_str, alt_result.get("recommendation"), alt_result.get("sources", []), confidence=alt_result.get("confidence_score", 1.0))
                except Exception as e:
                    logger.error(f"Alternative re-query failed: {e}")
                    alt_result = None
                    audit_token = log_audit_transaction(scenario_str, acr_result.get("recommendation"), acr_result.get("sources", []), confidence=acr_result.get("confidence_score", 1.0))

                return {
                    "status": "success_with_safety_requery",
                    "audit_token": audit_token,
                    "extracted_scenario": scenario_str,
                    "fhir_bundle": bundle_dict,
                    "original_recommendation_contraindicated": True,
                    "original_acr_recommendation": acr_result["recommendation"],
                    "contraindications": contraindications,
                    "alternative_recommendation": alt_result["recommendation"] if alt_result else None,
                    "alternative_sources": alt_result.get("sources", []) if alt_result else [],
                    "acr_sources": acr_result["sources"],
                    "draft_protocol": draft.to_dict(),
                }
        except Exception as e:
            logger.error(f"Safety loop processing error: {e}", exc_info=True)

    audit_token = log_audit_transaction(scenario_str, acr_result.get("recommendation"), acr_result.get("sources", []), confidence=acr_result.get("confidence_score", 1.0))
    logger.info(f"Successfully protocoled clinical scenario — Token: {audit_token}")
    return {
        "status": "success",
        "audit_token": audit_token,
        "extracted_scenario": scenario_str,
        "fhir_bundle": bundle_dict,
        "acr_recommendation": acr_result["recommendation"],
        "acr_sources": acr_result["sources"],
        "raw_query": acr_result.get("raw_query"),
        "expanded_query": acr_result.get("expanded_query"),
        "draft_protocol": draft.to_dict(),
    }



@app.post("/v1/override", dependencies=[Depends(verify_api_key)])
async def save_override(req: OverrideRequest):
    """Log a clinician override to the audit database."""
    logger.info("Received request to log override")
    try:
        add_clinician_override(
            query_key=req.query_key,
            original=req.original_recommendation,
            overridden=req.overridden_recommendation,
            reason=req.override_reason,
            notes=req.clinician_notes
        )
        return {"status": "success", "message": "Override logged successfully."}
    except Exception as e:
        logger.error("Error logging override", exc_info=True)
        raise HTTPException(status_code=500, detail="Error logging override. Check server logs for details.")


@app.post("/v1/accept-mapping", dependencies=[Depends(verify_api_key)])
async def accept_mapping(req: AcceptMappingRequest):
    """Log user acceptance of a protocol mapping and perform writeback if confidence is high."""
    logger.info(f"Received request to log mapping acceptance for '{req.acr_procedure_text}'")
    try:
        from protocol_db import log_mapping_acceptance
        log_mapping_acceptance(
            institution_id=req.institution_id,
            acr_scenario_text=req.acr_scenario_text,
            acr_procedure_text=req.acr_procedure_text,
            imaging_protocol_id=req.imaging_protocol_id,
            ir_protocol_id=req.ir_protocol_id,
            confidence_score=req.confidence_score,
            mapping_method=req.mapping_method,
            accepted_by=req.accepted_by
        )
        return {"status": "success", "message": "Mapping acceptance logged and processed."}
    except Exception as e:
        logger.error("Error logging mapping acceptance", exc_info=True)
        raise HTTPException(status_code=500, detail="Error logging mapping acceptance. Check server logs for details.")



@app.get("/v1/overrides", dependencies=[Depends(verify_api_key)])
async def list_overrides():
    """Retrieve audit history of overrides."""
    logger.info("Received request to list overrides")
    try:
        overrides = get_clinician_overrides()
        return {"status": "success", "overrides": overrides}
    except Exception as e:
        logger.error("Error retrieving overrides", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving overrides. Check server logs for details.")


@app.get("/v1/review/queue", dependencies=[Depends(verify_api_key)])
async def get_review_queue(status: Optional[str] = None):
    """Retrieve cases in the manual review queue."""
    logger.info(f"Received request to retrieve review queue (status={status})")
    import sqlite3
    conn = sqlite3.connect("data/query_cache.db", timeout=30.0)
    try:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                "SELECT id, session_id, scenario_text, confidence_score, abstention_reason, status, reviewer_id, reviewed_at, final_recommendation, created_at FROM manual_review_queue WHERE status = ? ORDER BY id DESC",
                (status,)
            )
        else:
            cursor.execute(
                "SELECT id, session_id, scenario_text, confidence_score, abstention_reason, status, reviewer_id, reviewed_at, final_recommendation, created_at FROM manual_review_queue ORDER BY id DESC"
            )
        rows = cursor.fetchall()
        
        queue = []
        for r in rows:
            queue.append({
                "id": r[0],
                "session_id": r[1],
                "scenario_text": r[2],
                "confidence_score": r[3],
                "abstention_reason": r[4],
                "status": r[5],
                "reviewer_id": r[6],
                "reviewed_at": r[7],
                "final_recommendation": r[8],
                "created_at": r[9]
            })
        return {"status": "success", "queue": queue}
    except Exception as e:
        logger.error("Error retrieving review queue", exc_info=True)
        raise HTTPException(status_code=500, detail="Error retrieving review queue. Check server logs for details.")
    finally:
        conn.close()


@app.post("/v1/review/claim", dependencies=[Depends(verify_api_key)])
async def claim_review_case(req: ClaimReviewRequest):
    """Claim a case in the manual review queue."""
    logger.info(f"Reviewer {req.reviewer_id} claiming session {req.session_id}")
    import sqlite3
    conn = sqlite3.connect("data/query_cache.db", timeout=30.0)
    try:
        # Check if already claimed or resolved
        row = conn.execute(
            "SELECT status, reviewer_id FROM manual_review_queue WHERE session_id = ?",
            (req.session_id,)
        ).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Case not found in review queue.")
        
        status, current_reviewer = row
        if status == "resolved":
            raise HTTPException(status_code=400, detail="Case has already been resolved.")
        
        if status == "claimed" and current_reviewer != req.reviewer_id:
            raise HTTPException(status_code=400, detail=f"Case has already been claimed by {current_reviewer}.")
            
        conn.execute(
            "UPDATE manual_review_queue SET status = 'claimed', reviewer_id = ? WHERE session_id = ?",
            (req.reviewer_id, req.session_id)
        )
        conn.commit()
        sync_cache_to_gcs()
        return {"status": "success", "message": f"Case claimed successfully by {req.reviewer_id}."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error claiming review case", exc_info=True)
        raise HTTPException(status_code=500, detail="Error claiming review case. Check server logs for details.")
    finally:
        conn.close()


@app.post("/v1/review/resolve", dependencies=[Depends(verify_api_key)])
async def resolve_review_case(req: ResolveReviewRequest):
    """Resolve a case in the manual review queue."""
    logger.info(f"Reviewer {req.reviewer_id} resolving session {req.session_id}")
    import sqlite3
    conn = sqlite3.connect("data/query_cache.db", timeout=30.0)
    try:
        # Check if exists and check claim status
        row = conn.execute(
            "SELECT status, reviewer_id FROM manual_review_queue WHERE session_id = ?",
            (req.session_id,)
        ).fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Case not found in review queue.")
            
        status, current_reviewer = row
        if status == "claimed" and current_reviewer != req.reviewer_id:
            raise HTTPException(status_code=400, detail=f"Case was claimed by {current_reviewer}. Only the claimant can resolve it.")
            
        reviewed_at = datetime.now().isoformat()
        conn.execute(
            "UPDATE manual_review_queue SET status = 'resolved', reviewer_id = ?, reviewed_at = ?, final_recommendation = ? WHERE session_id = ?",
            (req.reviewer_id, reviewed_at, req.final_recommendation, req.session_id)
        )
        conn.commit()
        sync_cache_to_gcs()
        return {
            "status": "success", 
            "message": "Case resolved successfully.",
            "reviewed_at": reviewed_at,
            "final_recommendation": req.final_recommendation
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error resolving review case", exc_info=True)
        raise HTTPException(status_code=500, detail="Error resolving review case. Check server logs for details.")
    finally:
        conn.close()


class CoPilotChatRequest(BaseModel):
    scenario_text: str = Field(..., max_length=4000)
    chat_history: list = Field(default_factory=list, max_length=20)
    message: str = Field(..., max_length=4000)


@app.post("/v1/copilot/chat")
@limiter.limit("30/minute")
async def copilot_chat(request: Request, req: CoPilotChatRequest):
    """Conversational Attending Radiology Co-Pilot endpoint."""
    logger.info("Received request on /v1/copilot/chat")
    if os.getenv("DISABLE_COPILOT", "false").strip().lower() == "true":
        return {
            "status": "success",
            "response": "The Attending Co-Pilot is currently deactivated to optimize token consumption during RAG engine testing."
        }
    await ensure_rag_ready()
    try:
        from copilot_engine import generate_copilot_response, CoPilotChatRequest as EngineRequest, ChatMessage
        
        history = []
        if isinstance(req.chat_history, list):
            for m in req.chat_history:
                history.append(ChatMessage(role=m.get("role", "user"), content=m.get("content", "")))
                
        engine_req = EngineRequest(
            scenario_text=req.scenario_text,
            chat_history=history,
            message=req.message
        )
        response_text = await asyncio.to_thread(generate_copilot_response, engine_req)
        return {"status": "success", "response": response_text}
    except Exception as e:
        logger.error("Error generating co-pilot response", exc_info=True)
        raise HTTPException(status_code=500, detail="Error generating co-pilot response. Check server logs for details.")


def fhir_bundle_from_cds_hook_payload(payload: dict) -> dict:
    """Extracts FHIR resources from a CDS Hooks request payload and compiles them into a standard FHIR Bundle."""
    resources = []
    
    def collect_resources(obj):
        if isinstance(obj, dict):
            if "resourceType" in obj:
                resources.append(obj)
            else:
                for k, v in obj.items():
                    collect_resources(v)
        elif isinstance(obj, list):
            for item in obj:
                collect_resources(item)
                
    # Collect from context (e.g., selections, draftOrders) and prefetch
    collect_resources(payload.get("context", {}))
    collect_resources(payload.get("prefetch", {}))
    
    # Filter resources and deduplicate by ID/resourceType
    seen = set()
    unique_entries = []
    for r in resources:
        # If it's a Bundle itself, extract its entries
        if r.get("resourceType") == "Bundle":
            for entry in r.get("entry", []):
                sub_res = entry.get("resource")
                if sub_res and "resourceType" in sub_res:
                    key = (sub_res["resourceType"], sub_res.get("id"))
                    if key not in seen:
                        seen.add(key)
                        unique_entries.append({"resource": sub_res})
        else:
            key = (r["resourceType"], r.get("id"))
            if key not in seen:
                seen.add(key)
                unique_entries.append({"resource": r})
                
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": unique_entries
    }


class CDSHookContext(BaseModel):
    """CDS Hooks context object."""
    userId: str = Field(..., max_length=200, description="User placing the order (e.g. Practitioner/123)")
    patientId: str = Field(..., max_length=200, description="Patient ID")
    selections: Optional[List[str]] = Field(default=None, description="Selected order references")

class CDSHookRequest(BaseModel):
    """CDS Hooks request payload validation model."""
    hook: str = Field(..., max_length=100, description="Hook type (e.g. order-select, order-sign)")
    hookInstance: Optional[str] = Field(default=None, max_length=200, description="Unique hook instance UUID")
    context: CDSHookContext
    prefetch: Optional[Dict[str, Any]] = Field(default=None, description="Prefetched FHIR resources")


@app.post("/v1/cds-hook")
@limiter.limit("30/minute")
async def cds_hook(request: Request, req: CDSHookRequest):
    """
    Dynamic CDS Hooks implementation (order-select / order-sign).
    Parses full EHR CDS Hook payload, runs RAG + Safety, and returns dynamic Cards.
    """
    logger.info("Received request on /v1/cds-hook")
    await ensure_rag_ready()
    try:
        payload = await request.json()
    except Exception as e:
        logger.warning(f"Failed to parse JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")
        
    # 1. Compile FHIR bundle from request
    bundle_dict = fhir_bundle_from_cds_hook_payload(payload)
    
    # 2. Extract context scenario text
    scenario_str = extract_scenario_from_bundle(bundle_dict)
    
    # Extract institution_id (or read from selection or default)
    context = payload.get("context", {})
    institution_id = payload.get("prefetch", {}).get("institution_id", {}).get("resource", {}).get("id")
    if not institution_id:
        institution_id = context.get("institutionId") or "skyridge"
        
    # 3. Query ACR guidelines
    try:
        acr_result = await asyncio.to_thread(query_acr_guidelines, scenario_str, bundle_dict)
    except Exception as e:
        logger.error("Error in RAG retrieval during CDS hook", exc_info=True)
        # Fall back to a default error card
        return {
            "cards": [
                {
                    "summary": "ACR Guidelines Retrieval Error",
                    "indicator": "warning",
                    "detail": "Failed to retrieve ACR appropriateness criteria. Please try again.",
                    "source": {"label": "ACR-AC-RAG Hub"}
                }
            ]
        }
        
    # 4. Map protocol and evaluate safety
    draft_protocol = None
    try:
        from protocol_mapper import get_draft_protocol
        draft_protocol = await asyncio.to_thread(
            get_draft_protocol,
            acr_result,
            bundle_dict,
            institution_id
        )
    except Exception as e:
        logger.error(f"Error mapping protocol during CDS hook: {e}", exc_info=True)
        
    # 5. Construct CDS Cards
    cards = []
    
    # Extract original ordered modality from ServiceRequest in selection
    original_service_request = None
    for entry in bundle_dict.get("entry", []):
        res = entry.get("resource", {})
        if res.get("resourceType") == "ServiceRequest":
            original_service_request = res
            break
            
    # A. Add Appropriateness Criteria Card
    rec_text = acr_result.get("recommendation", "")
    detail_text = f"**Clinical Scenario**: {scenario_str}\n\n**Guidelines Recommendation**:\n{rec_text}"
    
    appropriateness_card = {
        "summary": "ACR Appropriateness Criteria Recommendation",
        "indicator": "info",
        "detail": detail_text,
        "source": {
            "label": "American College of Radiology Appropriateness Criteria",
            "url": "https://www.acr.org/Clinical-Resources/ACR-Appropriateness-Criteria"
        }
    }
    
    # If the current selection doesn't match the recommended protocol, add a Suggestion
    if draft_protocol and original_service_request and draft_protocol.status in ("matched", "fuzzy_matched"):
        original_name = original_service_request.get("code", {}).get("text", "")
        recommended_name = draft_protocol.protocol_name
        
        # Check if they are substantially different
        if recommended_name and original_name and recommended_name.lower().strip() != original_name.lower().strip():
            # Add dynamic suggestion to change the order
            change_suggestion = {
                "label": f"Change order to recommended protocol: {recommended_name}",
                "uuid": "suggestion-replace-order",
                "actions": [
                    {
                        "type": "delete",
                        "description": f"Cancel original order for {original_name}",
                        "resource": f"ServiceRequest/{original_service_request.get('id', 'servicerequest-1')}"
                    },
                    {
                        "type": "create",
                        "description": f"Create new order for {recommended_name}",
                        "resource": {
                            "resourceType": "ServiceRequest",
                            "status": "draft",
                            "intent": "proposal",
                            "subject": {"reference": f"Patient/{bundle_dict.get('entry', [{}])[0].get('resource', {}).get('id', 'patient-1')}"},
                            "code": {
                                "concept": {
                                    "coding": [{"display": recommended_name}],
                                    "text": recommended_name
                                }
                            }
                        }
                    }
                ]
            }
            appropriateness_card["suggestions"] = [change_suggestion]
            
    cards.append(appropriateness_card)
    
    # B. Add Safety Alerts Cards
    if draft_protocol and draft_protocol.safety_profile:
        safety = draft_protocol.safety_profile
        
        # Triggered safety flags (e.g. eGFR, Allergy, Pregnancy, Radiation, Implant)
        for flag in safety.get("safety_flags", []):
            if flag.get("triggered"):
                severity = flag.get("severity", "warning")
                indicator = "critical" if severity == "hard_stop" else "warning"
                
                card = {
                    "summary": f"Patient Safety Flag: {flag.get('rule_type').replace('_', ' ').title()}",
                    "indicator": indicator,
                    "detail": f"**Alert**: {flag.get('message')}\n\n**Action Required**: {flag.get('action').replace('_', ' ').title()}",
                    "source": {"label": f"{institution_id.capitalize()} Safety Engine"}
                }
                cards.append(card)
                
        # Lab Checks failures
        for lab in safety.get("lab_checks", []):
            if not lab.get("is_met"):
                card = {
                    "summary": f"Contraindication: Inadequate {lab.get('lab_name')} Lab Value",
                    "indicator": "critical" if lab.get("action_if_not_met") == "hard_stop" else "warning",
                    "detail": f"Patient's {lab.get('lab_name')} is {lab.get('patient_value') or 'not found'} (Threshold: {lab.get('required_operator')} {lab.get('required_value')}).\n\n**Correction Guidance**: {lab.get('correction_guidance', 'Hold or reschedule.')}",
                    "source": {"label": f"{institution_id.capitalize()} Lab Check"}
                }
                cards.append(card)
                
        # Medication Holds required
        for hold in safety.get("med_holds", []):
            if hold.get("patient_is_taking"):
                card = {
                    "summary": f"Required Medication Hold: {hold.get('medication_name')}",
                    "indicator": "warning",
                    "detail": f"Patient is taking {hold.get('medication_name')}. Hold for **{hold.get('hold_hours_before')} hours before** procedure and resume **{hold.get('resume_hours_after') or 24} hours after**.",
                    "source": {"label": f"{institution_id.capitalize()} Anticoagulant Protocol"}
                }
                cards.append(card)
                
    return {"cards": cards}
