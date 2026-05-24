"""
Attending Radiology Co-Pilot Engine — Service E
================================================
A conversational clinical advisor utilizing the Google Gemini API 
and context retrieved from the ACR Appropriateness Criteria database.
"""

import os
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from google import genai
from google.genai import types
from dotenv import load_dotenv

from rag_engine import query_acr_guidelines, redact_phi

load_dotenv()

class ChatMessage(BaseModel):
    role: str                   # "user" or "model" / "assistant"
    content: str

class CoPilotChatRequest(BaseModel):
    scenario_text: str          # Patient clinical context
    chat_history: List[ChatMessage] = Field(default_factory=list)
    message: str                # Current user message/question

def get_gemini_client():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable is not set.")
    return genai.Client(api_key=api_key)

def generate_copilot_response(req: CoPilotChatRequest) -> str:
    """
    Generate a conversational response from the perspective of an Attending Radiologist
    using the patient context, current RAG guidelines, and chat history.
    """
    client = get_gemini_client()
    
    # 1. Retrieve the ACR Guidelines for the current patient context
    try:
        acr_result = query_acr_guidelines(req.scenario_text)
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
        print(f"[COPILOT WARNING] Failed to query ACR guidelines in co-pilot: {e}")
        acr_recommendation = "Unavailable due to retrieval error."
        guidelines_context = "No context retrieved."

    # 2. Build the system instruction and prompt
    system_instruction = (
        "You are an Attending Radiologist consulting with a clinical resident or ordering physician.\n"
        "You are discussing a clinical decision support case based on the American College of Radiology (ACR) Appropriateness Criteria.\n"
        "Your tone should be professional, academic, clinically helpful, and authoritative yet collaborative.\n"
        "Provide medically robust reasoning, referencing relative radiation levels (RRLs) or specific contraindications when appropriate.\n"
        "Do not offer definitive personal diagnoses, but guide the ordering provider on the most appropriate, safe imaging paths.\n"
        "Ensure all patient-identifying data remains redacted in your output."
    )

    # Compile the prompt including patient context and guidelines details
    prompt = (
        f"PATIENT CLINICAL PRESENTATION:\n{req.scenario_text}\n\n"
        f"ACR GUIDELINE RECOMMENDATION (SUMMARY):\n{acr_recommendation}\n\n"
        f"RELEVANT GUIDELINES NARRATIVE EXCERPTS:\n{guidelines_context}\n\n"
        f"CONVERSATION HISTORY:\n"
    )
    
    for msg in req.chat_history:
        role_label = "Resident/Clinician" if msg.role == "user" else "Attending Radiologist"
        prompt += f"{role_label}: {msg.content}\n"
        
    prompt += f"Resident/Clinician: {req.message}\n"
    prompt += "Attending Radiologist (Response):"

    # Redact PHI from input before sending to Gemini API
    redacted_prompt = redact_phi(prompt)

    # 3. Call the Gemini API
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=redacted_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                max_output_tokens=1000
            )
        )
        return response.text.strip()
    except Exception as e:
        print(f"[COPILOT ERROR] Gemini generation failed: {e}")
        return f"I apologize, but I am unable to consult on this case right now. (Error: {str(e)})"
