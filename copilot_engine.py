import os
import re
import logging
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from google import genai
from google.genai import types
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from rag_engine import query_acr_guidelines, redact_phi
from security_utils import check_prompt_injection

import time
import threading

__all__ = ["generate_copilot_response", "CoPilotChatRequest", "ChatMessage"]

logger = logging.getLogger("acr-ac-rag")

load_dotenv()

class SimpleTTLCache:
    def __init__(self, ttl_seconds=3600, max_size=512):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self.cache = {}
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key not in self.cache:
                return None
            val, expiry = self.cache[key]
            if time.time() > expiry:
                del self.cache[key]
                return None
            return val

    def set(self, key, value):
        with self.lock:
            now = time.time()
            expired_keys = [k for k, v in self.cache.items() if now > v[1]]
            for k in expired_keys:
                del self.cache[k]
            if len(self.cache) >= self.max_size:
                first_key = next(iter(self.cache))
                del self.cache[first_key]
            self.cache[key] = (value, now + self.ttl)

    def __contains__(self, key):
        return self.get(key) is not None

    def __getitem__(self, key):
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __setitem__(self, key, value):
        self.set(key, value)

# Thread-safe TTL cache for RAG results per scenario text (1 hour expiration, max 512 entries)
_rag_cache = SimpleTTLCache(ttl_seconds=3600, max_size=512)

_MAX_INPUT_LENGTH = 4000


def sanitize_clinical_input(text: str) -> str:
    """Sanitize free-text clinical input before it reaches the LLM.

    1. Checks *text* against known prompt-injection patterns and raises
       ``ValueError`` if any match is found.
    2. Truncates the input to a maximum of 4 000 characters.
    3. Wraps the (possibly truncated) text in sentinel boundary tags so the
       model can distinguish clinical data from instructions.

    Parameters
    ----------
    text : str
        Raw clinical scenario text supplied by the user.

    Returns
    -------
    str
        The sanitized and tagged text.

    Raises
    ------
    ValueError
        If the input matches a known prompt-injection pattern.
    """
    if check_prompt_injection(text):
        raise ValueError(
            "Input rejected: the clinical scenario text contains language "
            "that resembles a prompt-injection attempt. "
            "Please provide genuine clinical information only."
        )

    # Truncate overly long inputs
    text = text[:_MAX_INPUT_LENGTH]

    # Wrap in boundary tags so the LLM can distinguish data from instructions
    return f"[PATIENT_CLINICAL_DATA_START]\n{text}\n[PATIENT_CLINICAL_DATA_END]"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def _generate_content_with_retry(client, model, contents, config):
    return client.models.generate_content(model=model, contents=contents, config=config)


class ChatMessage(BaseModel):
    role: str                   # "user" or "model" / "assistant"
    content: str

class CoPilotChatRequest(BaseModel):
    scenario_text: str          # Patient clinical context
    chat_history: List[ChatMessage] = Field(default_factory=list)
    message: str                # Current user message/question

_gemini_client = None

def get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is not set.")
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client

def generate_copilot_response(req: CoPilotChatRequest) -> str:
    """
    Generate a conversational response from the perspective of an Attending Radiologist
    using the patient context, current RAG guidelines, and chat history.
    """
    # Sanitize clinical scenario text to guard against prompt injection
    try:
        sanitized_scenario = sanitize_clinical_input(req.scenario_text)
    except ValueError as e:
        return str(e)

    client = get_gemini_client()
    
    # 1. Retrieve the ACR Guidelines for the current patient context (reusing cache if present)
    try:
        # Redact PHI from the cache key to protect patient privacy
        cache_key = redact_phi(req.scenario_text).strip().lower()
        if cache_key in _rag_cache:
            acr_result = _rag_cache[cache_key]
            logger.info("[COPILOT CACHE HIT] Reusing guidelines context for conversation.")
        else:
            acr_result = query_acr_guidelines(req.scenario_text)
            _rag_cache[cache_key] = acr_result
            logger.info("[COPILOT CACHE MISS] Querying RAG and caching guidelines context for conversation.")
            
        acr_recommendation = acr_result.get("recommendation", "No specific recommendation retrieved.")
        sources = acr_result.get("sources", [])
        
        # Compile source content excerpts
        source_excerpts = []
        for i, src in enumerate(sources):
            content = src.get("content", "")
            meta = src.get("metadata", {})
            source_name = meta.get("source", "ACR Guideline")
            source_excerpts.append(f"--- Excerpt {i+1} (Source: {source_name}) ---\n{content}")
        guidelines_context = "\n\n".join(source_excerpts)
    except Exception as e:
        logger.warning(f"[COPILOT WARNING] Failed to query ACR guidelines in co-pilot: {e}")
        acr_recommendation = "Unavailable due to retrieval error."
        guidelines_context = "No context retrieved."

    # 2. Build the system instruction and prompt
    system_instruction = (
        "You are an AI-powered Protocol Co-Pilot, an assistant for radiology protocoling and clinical guidelines.\n"
        "You are discussing a clinical decision support case based on the American College of Radiology (ACR) Appropriateness Criteria.\n"
        "Your tone should be professional, academic, clinically helpful, and collaborative.\n"
        "Provide medically robust reasoning, referencing relative radiation levels (RRLs), scan parameters, local protocols, or specific contraindications when appropriate.\n"
        "Do not offer definitive personal diagnoses, but guide the clinician on the most appropriate, safe imaging paths and local protocols.\n"
        "Ensure all patient-identifying data remains redacted in your output."
    )

    # Compile the prompt including patient context and guidelines details
    prompt = (
        f"PATIENT CLINICAL PRESENTATION:\n{sanitized_scenario}\n\n"
        f"ACR GUIDELINE RECOMMENDATION (SUMMARY):\n{acr_recommendation}\n\n"
        f"RELEVANT GUIDELINES NARRATIVE EXCERPTS:\n{guidelines_context}\n\n"
        f"CONVERSATION HISTORY:\n"
    )
    
    for msg in req.chat_history:
        role_label = "Resident/Clinician" if msg.role == "user" else "Protocol Co-Pilot"
        prompt += f"{role_label}: {msg.content}\n"
        
    prompt += f"Resident/Clinician: {req.message}\n"
    prompt += "Protocol Co-Pilot (Response):"

    # Redact PHI from input before sending to Gemini API
    redacted_prompt = redact_phi(prompt)

    # 3. Call the Gemini API
    try:
        model_name = os.getenv("LLM_PRIMARY_MODEL", "gemini-2.5-flash")
        response = _generate_content_with_retry(
            client,
            model=model_name,
            contents=redacted_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                max_output_tokens=1000
            )
        )
        disclaimer = (
            "\n\n---\n*Disclaimer: This output is generated by an AI system referencing published ACR Appropriateness Criteria. "
            "It does not constitute clinical advice and does not substitute for physician judgment. Always verify recommendations "
            "against current guidelines and patient context.*"
        )
        return response.text.strip() + disclaimer
    except Exception as e:
        logger.error(f"[COPILOT ERROR] Gemini generation failed: {e}")
        return f"I apologize, but I am unable to consult on this case right now. (Error: {str(e)})"
