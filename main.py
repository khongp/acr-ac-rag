import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
import os
import logging
from logging.handlers import RotatingFileHandler

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

from fhir_converter import convert_text_to_fhir_bundle, extract_scenario_from_bundle
from rag_engine import query_acr_guidelines, init_rag, add_clinician_override, get_clinician_overrides


# Priority 3: Eager model loading at startup
# Eliminates the 30-40s cold-start penalty on first request
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load ML models in a background thread during startup."""
    logger.info("[STARTUP] Pre-loading RAG models (embeddings, reranker, LLM)...")
    
    # Diagnostics check on assets existence
    if not os.path.exists("chroma_db_gemini") and not os.path.exists("chroma_db_local"):
         logger.warning("[STARTUP WARNING] Vector database directory not found in current workspace. Ensure ingest.py has been run.")
         
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, init_rag)
    logger.info("[READY] All models loaded — server ready for requests")
    yield
    # Shutdown: nothing to clean up


app = FastAPI(
    title="ACR-AC-RAG Headless API",
    description="FHIR and CDS Hooks compliant backend for ACR guidelines retrieval and protocoling assistance.",
    version="2.1.0",
    lifespan=lifespan,
)

# Hardened CORS origins setup
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "")
if allowed_origins_env:
    allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]
else:
    # Strict default list for development and production safety
    allowed_origins = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://localhost:5173",  # Vite dev server
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AnalyzeRequest(BaseModel):
    text: Optional[str] = None
    bundle: Optional[Dict[str, Any]] = None

class ProtocolRequest(BaseModel):
    text: Optional[str] = None
    bundle: Optional[Dict[str, Any]] = None
    institution_id: Optional[str] = None   # Override default institution

class OverrideRequest(BaseModel):
    query_key: str
    original_recommendation: str
    overridden_recommendation: str
    override_reason: str
    clinician_notes: str = ""


@app.get("/", response_class=HTMLResponse)
async def get_index():
    """Serve the premium clinical decision support hub dashboard."""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), status_code=200)
    return HTMLResponse(content="<h1>Frontend index.html not found</h1>", status_code=404)



@app.post("/v1/analyze")
async def analyze_scenario(req: AnalyzeRequest):
    """
    Analyzes a clinical scenario. Accepts either raw text or a FHIR Bundle.
    Returns the ACR recommendation.
    """
    logger.info("Received request on /v1/analyze")
    if not req.text and not req.bundle:
        logger.warning("AnalyzeRequest missing both 'text' and 'bundle'")
        raise HTTPException(status_code=400, detail="Must provide either 'text' or 'bundle'.")

    # 1. Standardize to FHIR Bundle format if text is provided
    if req.text:
        try:
            bundle_obj = convert_text_to_fhir_bundle(req.text)
            bundle_dict = bundle_obj.model_dump() # Pydantic V2
        except Exception as e:
            logger.error("Error in Text-to-FHIR conversion", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error in Text-to-FHIR conversion: {str(e)}")
    else:
        bundle_dict = req.bundle

    # 2. Extract context string for the RAG engine
    scenario_str = extract_scenario_from_bundle(bundle_dict)

    # 3. Retrieve and Generate
    try:
        result = query_acr_guidelines(scenario_str)
    except Exception as e:
        logger.error("Error in RAG retrieval", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error in RAG retrieval: {str(e)}")

    logger.info("Successfully analyzed clinical scenario")
    return {
        "status": "success",
        "mock_bundle_used": bundle_dict if req.text else None,
        "extracted_scenario": scenario_str,
        "recommendation": result["recommendation"],
        "sources": result["sources"]
    }


@app.post("/v1/protocol")
async def get_draft_protocol_endpoint(req: ProtocolRequest):
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
    if not req.text and not req.bundle:
        logger.warning("ProtocolRequest missing both 'text' and 'bundle'")
        raise HTTPException(status_code=400, detail="Must provide either 'text' or 'bundle'.")

    # 1. Convert to FHIR Bundle
    if req.text:
        try:
            bundle_obj = convert_text_to_fhir_bundle(req.text)
            bundle_dict = bundle_obj.model_dump()
        except Exception as e:
            logger.error("Error in Text-to-FHIR conversion", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error in Text-to-FHIR conversion: {str(e)}")
    else:
        bundle_dict = req.bundle

    # 2. Extract scenario for RAG
    scenario_str = extract_scenario_from_bundle(bundle_dict)

    # 3. Run ACR RAG Engine
    try:
        acr_result = query_acr_guidelines(scenario_str)
    except Exception as e:
        logger.error("Error in RAG retrieval", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error in RAG retrieval: {str(e)}")

    # 4. Run Protocol Mapper (Service C)
    try:
        from protocol_mapper import get_draft_protocol
        draft = get_draft_protocol(
            acr_result=acr_result,
            fhir_bundle=bundle_dict,
            institution_id=req.institution_id,
        )
    except Exception as e:
        logger.error("Protocol mapping failed — falling back to partial success", exc_info=True)
        # Protocol mapping is additive — return ACR result even if mapping fails
        return {
            "status": "partial_success",
            "extracted_scenario": scenario_str,
            "fhir_bundle": bundle_dict,
            "acr_recommendation": acr_result["recommendation"],
            "acr_sources": acr_result["sources"],
            "draft_protocol": None,
            "protocol_error": str(e),
        }

    logger.info("Successfully protocoled clinical scenario")
    return {
        "status": "success",
        "extracted_scenario": scenario_str,
        "fhir_bundle": bundle_dict,
        "acr_recommendation": acr_result["recommendation"],
        "acr_sources": acr_result["sources"],
        "draft_protocol": draft.to_dict(),
    }


@app.post("/v1/override")
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
        raise HTTPException(status_code=500, detail=f"Error logging override: {str(e)}")


@app.get("/v1/overrides")
async def list_overrides():
    """Retrieve audit history of overrides."""
    logger.info("Received request to list overrides")
    try:
        overrides = get_clinician_overrides()
        return {"status": "success", "overrides": overrides}
    except Exception as e:
        logger.error("Error retrieving overrides", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error retrieving overrides: {str(e)}")


@app.post("/v1/cds-hook")
async def cds_hook(request: Request):
    """
    Minimal CDS Hooks implementation (order-select).
    Returns a mocked Information Card initially, as planned.
    """
    payload = await request.json()
    
    # Phase 1: Minimal parsing
    context = payload.get("context", {})
    selections = context.get("selections", [])
    
    response_cards = {
        "cards": [
            {
                "summary": "ACR Appropriateness Criteria Consultation",
                "indicator": "info",
                "detail": "Connection to the ACR-AC-RAG backend was successful. The patient's draft orders have been acknowledged.",
                "source": {
                    "label": "ACR-AC-RAG Service",
                    "url": "https://acr.org/"
                }
            }
        ]
    }
    
    return response_cards
