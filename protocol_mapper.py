"""
Protocol Mapper — Service C
============================
The bridge between the ACR Brain (Service A / RAG Engine) and the 
hospital-specific Protocol Library (SQLite DB).

Workflow:
  1. Receives ACR recommendation output + FHIR patient bundle
  2. Searches the acr_protocol_map for matching protocols  
  3. Falls back to LLM-assisted fuzzy matching if no exact match found
  4. Runs the Safety Engine against patient data
  5. Returns a DraftProtocol with confidence score

This is the core "protocoling assistant" — the Holy Grail.
"""

import os
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from datetime import datetime
from dotenv import load_dotenv

from protocol_db import (
    lookup_protocol_by_acr,
    search_protocols_fulltext,
    get_contrast_rules,
    get_ir_protocol_details,
    get_protocol_steps,
)
from safety_engine import (
    extract_patient_safety_data,
    evaluate_safety,
    SafetyProfile,
)

load_dotenv()

# Default institution — override via INSTITUTION_ID env var
DEFAULT_INSTITUTION = os.environ.get("INSTITUTION_ID", "skyridge")


@dataclass
class DraftProtocol:
    """
    The final output: a pre-populated protocol recommendation 
    ready for resident review and one-click approval.
    """
    status: str                             # "matched", "fuzzy_matched", "no_match"
    institution_id: str
    confidence_score: float                 # 0.0 – 1.0
    
    # ACR source (what the RAG engine said)
    acr_scenario: Optional[str] = None
    acr_procedure: Optional[str] = None
    acr_appropriateness: Optional[str] = None
    acr_recommendation_text: Optional[str] = None
    
    # Protocol details (the recipe)
    protocol_id: Optional[str] = None
    protocol_name: Optional[str] = None
    protocol_type: Optional[str] = None     # "imaging" or "ir"
    protocol_details: Dict[str, Any] = field(default_factory=dict)
    protocol_steps: List[Dict[str, Any]] = field(default_factory=list)
    scanner_id: Optional[str] = None
    scanner_type: Optional[str] = None
    
    # Safety evaluation
    safety_profile: Optional[Dict[str, Any]] = None
    
    # Metadata
    mapping_method: Optional[str] = None    # "manual_review", "automated_fuzzy_match", "llm_assisted"
    mapped_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> dict:
        return asdict(self)


def get_draft_protocol(
    acr_result: Dict[str, Any],
    fhir_bundle: Dict[str, Any],
    institution_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> DraftProtocol:
    """
    Main entry point: takes RAG output + FHIR bundle → returns a DraftProtocol.
    
    Args:
        acr_result: Output from query_acr_guidelines() — must contain:
            - "recommendation": str (full RAG response text)
            - "sources": list of source documents
        fhir_bundle: FHIR Bundle dict (from fhir_converter or live EHR)
        institution_id: Hospital ID (defaults to INSTITUTION_ID env var)
        db_path: Optional explicit path to protocol database
    
    Returns:
        DraftProtocol with protocol details and safety evaluation
    """
    institution = institution_id or DEFAULT_INSTITUTION
    recommendation_text = acr_result.get("recommendation", "")
    
    # Step 1: Extract ACR procedure and scenario from RAG output
    acr_procedure, acr_scenario = _extract_acr_identifiers(acr_result)
    
    # Step 2: Look up protocol in the bridge table
    matches = []
    if acr_procedure:
        matches = lookup_protocol_by_acr(
            acr_procedure_text=acr_procedure,
            institution_id=institution,
            acr_scenario_text=acr_scenario,
            db_path=db_path,
        )
        # Fallback to scenario-less lookup if no exact match found
        if not matches:
            matches = lookup_protocol_by_acr(
                acr_procedure_text=acr_procedure,
                institution_id=institution,
                acr_scenario_text=None,
                db_path=db_path,
            )
    
    # Step 3: Extract patient safety data from FHIR bundle
    patient_data = extract_patient_safety_data(fhir_bundle)
    
    # Step 4: Build the DraftProtocol
    if matches:
        # Use the highest-confidence match
        best = matches[0]
        protocol_type = "imaging" if best.get("imaging_protocol_id") else "ir"
        protocol_id = best.get("imaging_protocol_id") or best.get("ir_protocol_id")
        
        # Get protocol steps if imaging
        steps = []
        if protocol_type == "imaging" and protocol_id:
            steps = get_protocol_steps(protocol_id, db_path)
        
        # Run safety evaluation
        safety = evaluate_safety(best, patient_data, data_source="synthetic")
        
        draft = DraftProtocol(
            status="matched",
            institution_id=institution,
            confidence_score=best.get("match_confidence", 0.0),
            acr_scenario=acr_scenario,
            acr_procedure=acr_procedure,
            acr_appropriateness=best.get("acr_appropriateness"),
            acr_recommendation_text=recommendation_text,
            protocol_id=protocol_id,
            protocol_name=best.get("protocol_name") or best.get("name"),
            protocol_type=protocol_type,
            protocol_details=_clean_protocol_details(best),
            protocol_steps=steps,
            safety_profile=safety.to_dict(),
            mapping_method=best.get("mapping_method"),
            scanner_id=best.get("scanner_id"),
            scanner_type=best.get("scanner_type"),
        )
        return draft
    
    # Step 5: Fallback — LLM-assisted fuzzy matching
    fuzzy_result = _llm_fuzzy_match(
        acr_procedure=acr_procedure,
        acr_scenario=acr_scenario,
        institution_id=institution,
        db_path=db_path,
    )
    
    if fuzzy_result:
        protocol_id = fuzzy_result.get("protocol_id")
        protocol_type = fuzzy_result.get("protocol_type", "imaging")
        
        # Fetch full protocol details
        if protocol_type == "imaging":
            from protocol_db import get_connection
            with get_connection(db_path) as conn:
                row = conn.execute(
                    "SELECT ip.*, s.model as scanner_type FROM imaging_protocol ip LEFT JOIN scanner s ON ip.scanner_id = s.id WHERE ip.id = ?", [protocol_id]
                ).fetchone()
                if row:
                    protocol_details = dict(row)
                    protocol_details["contrast_rules"] = get_contrast_rules(protocol_id, db_path)
                else:
                    protocol_details = {}
        else:
            protocol_details = get_ir_protocol_details(protocol_id, db_path)
        
        steps = get_protocol_steps(protocol_id, db_path) if protocol_type == "imaging" else []
        safety = evaluate_safety(protocol_details, patient_data, data_source="synthetic")
        
        draft = DraftProtocol(
            status="fuzzy_matched",
            institution_id=institution,
            confidence_score=fuzzy_result.get("confidence", 0.5),
            acr_scenario=acr_scenario,
            acr_procedure=acr_procedure,
            acr_recommendation_text=recommendation_text,
            protocol_id=protocol_id,
            protocol_name=fuzzy_result.get("protocol_name"),
            protocol_type=protocol_type,
            protocol_details=protocol_details,
            protocol_steps=steps,
            safety_profile=safety.to_dict(),
            mapping_method="llm_assisted",
            scanner_id=protocol_details.get("scanner_id"),
            scanner_type=protocol_details.get("scanner_type"),
        )
        return draft
    
    # Step 6: No match at all
    safety = evaluate_safety({}, patient_data, data_source="synthetic")
    return DraftProtocol(
        status="no_match",
        institution_id=institution,
        confidence_score=0.0,
        acr_scenario=acr_scenario,
        acr_procedure=acr_procedure,
        acr_recommendation_text=recommendation_text,
        safety_profile=safety.to_dict(),
    )


def _extract_acr_identifiers(acr_result: dict) -> tuple:
    """
    Extract the primary ACR procedure and scenario from the RAG output.
    Parses both the recommendation text and the source metadata.
    
    Returns (acr_procedure, acr_scenario) — both may be None.
    """
    acr_procedure = None
    acr_scenario = None
    
    # Try to extract from source documents metadata
    sources = acr_result.get("sources", [])
    for source in sources:
        content = source.get("content", "")
        metadata = source.get("metadata", {})
        
        # Check metadata first
        if metadata.get("type") == "variant_table":
            if "scenario" in metadata and not acr_scenario:
                acr_scenario = metadata["scenario"]
        
        # Look for structured variant table data in content
        if "ACR Appropriateness Table Data" in content:
            lines = content.split("\n")
            for line in lines:
                if line.startswith("Procedure:"):
                    proc = line.replace("Procedure:", "").strip()
                    if proc and not acr_procedure:
                        acr_procedure = proc
                elif line.startswith("Clinical Scenario (Variant):"):
                    scen = line.replace("Clinical Scenario (Variant):", "").strip()
                    if scen and not acr_scenario:
                        acr_scenario = scen
    
    # Fallback: try to extract modality keywords from recommendation text
    if not acr_procedure:
        rec_text = acr_result.get("recommendation", "").lower()
        # Look for common modality + body region patterns
        modality_patterns = [
            "ct abdomen and pelvis", "ct head", "cta head", "cta chest",
            "mri brain", "mri head", "us abdomen", "ct chest",
            "mri spine", "ct spine", "us pelvis",
        ]
        for pattern in modality_patterns:
            if pattern in rec_text:
                acr_procedure = pattern.title()
                break
    
    return acr_procedure, acr_scenario


def _clean_protocol_details(match: dict) -> dict:
    """Remove bridge table metadata, keep only protocol-relevant fields."""
    exclude_keys = {
        "id", "imaging_protocol_id", "ir_protocol_id", "acr_scenario_text",
        "acr_procedure_text", "acr_appropriateness", "match_confidence",
        "mapping_method", "mapped_by", "notes", "is_active",
        "created_at", "updated_at", "institution_id",
    }
    return {k: v for k, v in match.items() if k not in exclude_keys and v is not None}


def _llm_fuzzy_match(
    acr_procedure: Optional[str],
    acr_scenario: Optional[str],
    institution_id: str,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    LLM-assisted fuzzy matching: when no exact bridge mapping exists,
    use Gemini to match the ACR procedure text to available protocols.
    
    Returns {"protocol_id": ..., "protocol_name": ..., "protocol_type": ..., "confidence": ...}
    or None if no match.
    """
    if not acr_procedure:
        return None
    
    # Get all available protocols for this institution
    search_term = acr_procedure.split()[0] if acr_procedure else ""
    candidates = search_protocols_fulltext(institution_id, search_term, db_path)
    
    if not candidates:
        # Try broader search
        candidates = search_protocols_fulltext(institution_id, "", db_path)
    
    if not candidates:
        return None
    
    # Format candidates for LLM
    candidate_text = "\n".join(
        f"  {i+1}. [{c['protocol_type'].upper()}] ID: {c['id']} — {c['name']} "
        f"(Modality: {c.get('modality','N/A')}, Region: {c.get('body_region','N/A')})"
        for i, c in enumerate(candidates)
    )
    
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.0)
        
        prompt = f"""You are a radiology protocol matching assistant.

Given an ACR Appropriateness Criteria recommendation, match it to the most appropriate 
hospital-specific protocol from the list below.

ACR Procedure: {acr_procedure}
ACR Clinical Scenario: {acr_scenario or 'Not specified'}

Available Hospital Protocols:
{candidate_text}

Respond ONLY with a JSON object (no markdown, no explanation):
{{
    "matched_id": "<protocol ID or null if no good match>",
    "confidence": <0.0 to 1.0>,
    "reasoning": "<brief explanation>"
}}

Rules:
- Match based on modality, body region, and clinical context
- Confidence > 0.7 only if strong match
- Return null for matched_id if no protocol is a reasonable match
"""
        
        response = llm.invoke(prompt)
        content = response.content
        if isinstance(content, list):
            response_text = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            ).strip()
        else:
            response_text = str(content).strip()
        
        # Clean any markdown formatting
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            response_text = response_text.rsplit("```", 1)[0]
        
        result = json.loads(response_text)
        
        if result.get("matched_id"):
            # Find the matched candidate
            matched = next(
                (c for c in candidates if c["id"] == result["matched_id"]),
                None
            )
            if matched:
                return {
                    "protocol_id": matched["id"],
                    "protocol_name": matched["name"],
                    "protocol_type": matched.get("protocol_type", "imaging"),
                    "confidence": result.get("confidence", 0.5),
                    "reasoning": result.get("reasoning", ""),
                }
        
        return None
        
    except Exception as e:
        print(f"⚠️ LLM fuzzy matching failed: {e}")
        return None


# ─────────────────────────────────────────────
# Batch Mapping Tool (for initial protocol library setup)
# ─────────────────────────────────────────────

def batch_map_acr_to_protocols(
    acr_variants_path: str,
    institution_id: str,
    db_path: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = True,
) -> List[Dict[str, Any]]:
    """
    LLM-assisted batch mapping tool: reads all unique ACR procedure texts 
    from acr_variant_tables.json and proposes protocol matches for human review.
    
    Args:
        acr_variants_path: Path to acr_variant_tables.json
        institution_id: Target hospital
        limit: Max number of unique procedures to process (for testing)
        dry_run: If True, only print proposals. If False, insert into DB.
    
    Returns:
        List of proposed mappings
    """
    import json as json_mod
    
    with open(acr_variants_path, 'r', encoding='utf-8') as f:
        data = json_mod.load(f)
    
    # Extract unique procedure texts
    unique_procedures = set()
    for topic in data:
        for variant in topic.get("variantData", []):
            proc = variant.get("Procedure", "")
            scenario = variant.get("Scenario", "")
            appropriateness = variant.get("Appropriateness Category", "")
            if proc and "Usually appropriate" in appropriateness:
                unique_procedures.add((scenario, proc))
    
    print(f"Found {len(unique_procedures)} unique 'Usually Appropriate' ACR procedures")
    
    if limit:
        unique_procedures = list(unique_procedures)[:limit]
    
    proposals = []
    for scenario, procedure in unique_procedures:
        match = _llm_fuzzy_match(
            acr_procedure=procedure,
            acr_scenario=scenario,
            institution_id=institution_id,
            db_path=db_path,
        )
        
        proposal = {
            "acr_scenario": scenario,
            "acr_procedure": procedure,
            "matched_protocol_id": match.get("protocol_id") if match else None,
            "matched_protocol_name": match.get("protocol_name") if match else None,
            "confidence": match.get("confidence", 0.0) if match else 0.0,
            "reasoning": match.get("reasoning", "") if match else "No match found",
        }
        proposals.append(proposal)
        
        status = "✅" if proposal["confidence"] >= 0.7 else "⚠️" if proposal["confidence"] >= 0.4 else "❌"
        print(f"  {status} {procedure} → {proposal['matched_protocol_name'] or 'NO MATCH'} ({proposal['confidence']:.0%})")
    
    if not dry_run:
        from protocol_db import get_connection
        with get_connection(db_path) as conn:
            for p in proposals:
                if p["matched_protocol_id"] and p["confidence"] >= 0.7:
                    # Determine protocol type
                    row = conn.execute(
                        "SELECT id FROM imaging_protocol WHERE id = ?",
                        [p["matched_protocol_id"]]
                    ).fetchone()
                    
                    if row:
                        conn.execute("""
                            INSERT OR IGNORE INTO acr_protocol_map 
                            (institution_id, acr_scenario_text, acr_procedure_text,
                             imaging_protocol_id, match_confidence, mapping_method, mapped_by)
                            VALUES (?, ?, ?, ?, ?, 'llm_assisted', 'system_batch_v1')
                        """, [institution_id, p["acr_scenario"], p["acr_procedure"],
                              p["matched_protocol_id"], p["confidence"]])
                    else:
                        conn.execute("""
                            INSERT OR IGNORE INTO acr_protocol_map 
                            (institution_id, acr_scenario_text, acr_procedure_text,
                             ir_protocol_id, match_confidence, mapping_method, mapped_by)
                            VALUES (?, ?, ?, ?, ?, 'llm_assisted', 'system_batch_v1')
                        """, [institution_id, p["acr_scenario"], p["acr_procedure"],
                              p["matched_protocol_id"], p["confidence"]])
        
        print(f"\n✅ Inserted {sum(1 for p in proposals if p['confidence'] >= 0.7)} high-confidence mappings")
    
    return proposals


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "batch-map":
        acr_path = sys.argv[2] if len(sys.argv) > 2 else "data/acr_variant_tables.json"
        institution = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_INSTITUTION
        limit = int(sys.argv[4]) if len(sys.argv) > 4 else 10
        
        print(f"Running batch mapping: {acr_path} → {institution} (limit: {limit})")
        batch_map_acr_to_protocols(acr_path, institution, limit=limit, dry_run=True)
    else:
        print("Usage: python protocol_mapper.py batch-map [acr_json_path] [institution_id] [limit]")
