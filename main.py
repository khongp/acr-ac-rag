import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any, Optional
import os

from fhir_converter import convert_text_to_fhir_bundle, extract_scenario_from_bundle
from rag_engine import query_acr_guidelines, init_rag


# Priority 3: Eager model loading at startup
# Eliminates the 30-40s cold-start penalty on first request
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load ML models in a background thread during startup."""
    print("[STARTUP] Pre-loading RAG models (embeddings, reranker, LLM)...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, init_rag)
    print("[READY] All models loaded — server ready for requests")
    yield
    # Shutdown: nothing to clean up


app = FastAPI(
    title="ACR-AC-RAG Headless API",
    description="FHIR and CDS Hooks compliant backend for ACR guidelines retrieval and protocoling assistance.",
    version="2.1.0",
    lifespan=lifespan,
)

# Enable CORS for frontend integrations
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    if not req.text and not req.bundle:
        raise HTTPException(status_code=400, detail="Must provide either 'text' or 'bundle'.")

    # 1. Standardize to FHIR Bundle format if text is provided
    if req.text:
        try:
            bundle_obj = convert_text_to_fhir_bundle(req.text)
            bundle_dict = bundle_obj.model_dump() # Pydantic V2
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error in Text-to-FHIR conversion: {str(e)}")
    else:
        bundle_dict = req.bundle

    # 2. Extract context string for the RAG engine
    scenario_str = extract_scenario_from_bundle(bundle_dict)

    # 3. Retrieve and Generate
    try:
        result = query_acr_guidelines(scenario_str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in RAG retrieval: {str(e)}")

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
    if not req.text and not req.bundle:
        raise HTTPException(status_code=400, detail="Must provide either 'text' or 'bundle'.")

    # 1. Convert to FHIR Bundle
    if req.text:
        try:
            bundle_obj = convert_text_to_fhir_bundle(req.text)
            bundle_dict = bundle_obj.model_dump()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error in Text-to-FHIR conversion: {str(e)}")
    else:
        bundle_dict = req.bundle

    # 2. Extract scenario for RAG
    scenario_str = extract_scenario_from_bundle(bundle_dict)

    # 3. Run ACR RAG Engine
    try:
        acr_result = query_acr_guidelines(scenario_str)
    except Exception as e:
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

    return {
        "status": "success",
        "extracted_scenario": scenario_str,
        "fhir_bundle": bundle_dict,
        "acr_recommendation": acr_result["recommendation"],
        "acr_sources": acr_result["sources"],
        "draft_protocol": draft.to_dict(),
    }


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
