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
rag_initialized = False
rag_error = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load ML models in a background thread during startup."""
    global rag_initialized, rag_error
    logger.info("[STARTUP] Pre-loading RAG models (embeddings, reranker, LLM)...")
    
    # Diagnostics check on assets existence
    if not os.path.exists("chroma_db_gemini") and not os.path.exists("chroma_db_local"):
         logger.warning("[STARTUP WARNING] Vector database directory not found in current workspace. Ensure ingest.py has been run.")
         
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, init_rag)
        rag_initialized = True
        logger.info("[READY] All models loaded — server ready for requests")
    except Exception as e:
        rag_error = str(e)
        logger.critical(f"[CRITICAL STARTUP ERROR] RAG model initialization failed: {e}", exc_info=True)
        
    yield
    # Shutdown: nothing to clean up


app = FastAPI(
    title="ACR-AC-RAG Headless API",
    description="FHIR and CDS Hooks compliant backend for ACR guidelines retrieval and protocoling assistance.",
    version="2.1.0",
    lifespan=lifespan,
)

# Permissive CORS origins setup to prevent connection errors across environments
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "")
if allowed_origins_env:
    allowed_origins = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    # If not explicitly configured, fall back to permissive matching for easy cross-origin connection
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://.*",
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
            "raw_query": acr_result.get("raw_query"),
            "expanded_query": acr_result.get("expanded_query"),
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
        "raw_query": acr_result.get("raw_query"),
        "expanded_query": acr_result.get("expanded_query"),
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


class CoPilotChatRequest(BaseModel):
    scenario_text: str
    chat_history: list = []
    message: str


@app.post("/v1/copilot/chat")
async def copilot_chat(req: CoPilotChatRequest):
    """Conversational Attending Radiology Co-Pilot endpoint."""
    logger.info("Received request on /v1/copilot/chat")
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
        response_text = generate_copilot_response(engine_req)
        return {"status": "success", "response": response_text}
    except Exception as e:
        logger.error("Error generating co-pilot response", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error generating co-pilot response: {str(e)}")


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


@app.post("/v1/cds-hook")
async def cds_hook(request: Request):
    """
    Dynamic CDS Hooks implementation (order-select / order-sign).
    Parses full EHR CDS Hook payload, runs RAG + Safety, and returns dynamic Cards.
    """
    logger.info("Received request on /v1/cds-hook")
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
        acr_result = query_acr_guidelines(scenario_str)
    except Exception as e:
        logger.error("Error in RAG retrieval during CDS hook", exc_info=True)
        # Fall back to a default error card
        return {
            "cards": [
                {
                    "summary": "ACR Guidelines Retrieval Error",
                    "indicator": "warning",
                    "detail": f"Failed to retrieve ACR appropriateness criteria: {str(e)}",
                    "source": {"label": "ACR-AC-RAG Hub"}
                }
            ]
        }
        
    # 4. Map protocol and evaluate safety
    draft_protocol = None
    try:
        from protocol_mapper import get_draft_protocol
        draft_protocol = get_draft_protocol(
            acr_result=acr_result,
            fhir_bundle=bundle_dict,
            institution_id=institution_id
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
