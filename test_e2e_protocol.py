"""
End-to-End Protocol Test — The Holy Grail Demo
================================================
Tests the full pipeline: Clinical Text → FHIR → RAG → Protocol Mapper → Draft Protocol
with a realistic clinical scenario that triggers multiple safety checks.
"""

import sys
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

import requests
import json

API_URL = "http://localhost:8000/v1/protocol"

# ─────────────────────────────────────────────
# Test Case 1: RLQ Pain — CT Appendicitis
# Complex scenario: allergy, medication, pregnancy risk
# ─────────────────────────────────────────────

scenario_1 = (
    "28 year old female presenting with acute right lower quadrant pain, "
    "nausea, and fever. History of allergy to Omnipaque (hives). "
    "eGFR 62. Currently taking Eliquis 5mg BID for DVT prophylaxis. "
    "Pregnancy test pending."
)

# ─────────────────────────────────────────────
# Test Case 2: Acute Stroke — CTA Head/Neck
# ─────────────────────────────────────────────

scenario_2 = (
    "72 year old male with sudden onset left-sided weakness and slurred speech. "
    "NIHSS 14. Last known well 2 hours ago. eGFR 28. "
    "On Warfarin for atrial fibrillation, INR 2.8."
)

# ─────────────────────────────────────────────
# Test Case 3: Suspected PE
# ─────────────────────────────────────────────

scenario_3 = (
    "45 year old male, 3 days post right knee arthroplasty, "
    "acute onset chest pain and dyspnea. D-dimer elevated at 4200. "
    "Heart rate 118. SpO2 91% on room air. eGFR 95. No known allergies."
)


def run_test(name, scenario):
    print("\n" + "=" * 70)
    print(f"  {name}")
    print("=" * 70)
    print(f"\n  SCENARIO: {scenario}\n")
    
    try:
        resp = requests.post(API_URL, json={"text": scenario}, timeout=120)
        data = resp.json()
    except requests.exceptions.ConnectionError:
        print("  ❌ Cannot connect to API. Is uvicorn running on port 8000?")
        return
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return
    
    status = data.get("status", "unknown")
    print(f"  API Status: {status}")
    
    if status == "partial_success":
        print(f"  Protocol Error: {data.get('protocol_error', 'N/A')}")
    
    # ── ACR Recommendation ──
    rec = data.get("acr_recommendation", "")
    print("\n  ┌─── ACR RECOMMENDATION ───")
    for line in rec.split("\n")[:25]:
        print(f"  │ {line}")
    print("  └" + "─" * 40)
    
    # ── FHIR Bundle Summary ──
    bundle = data.get("fhir_bundle", {})
    entries = bundle.get("entry", [])
    resource_types = [e.get("resource", {}).get("resourceType", "?") for e in entries]
    print(f"\n  FHIR Resources Extracted: {', '.join(resource_types)}")
    
    # ── Draft Protocol ──
    draft = data.get("draft_protocol")
    if not draft:
        print("\n  ⚠️ No draft protocol returned")
        return
    
    print("\n  ┌─── DRAFT PROTOCOL ───")
    print(f"  │ Match Status:  {draft.get('status', 'N/A')}")
    print(f"  │ Confidence:    {draft.get('confidence_score', 0):.0%}")
    print(f"  │ Protocol:      {draft.get('protocol_name', 'N/A')}")
    print(f"  │ Protocol ID:   {draft.get('protocol_id', 'N/A')}")
    print(f"  │ Type:          {draft.get('protocol_type', 'N/A')}")
    print(f"  │ Method:        {draft.get('mapping_method', 'N/A')}")
    
    details = draft.get("protocol_details", {})
    if details.get("contrast_type"):
        print(f"  │")
        print(f"  │ Contrast:      {details.get('contrast_type', 'N/A')} — {details.get('contrast_agent', 'N/A')}")
        print(f"  │ Volume/Rate:   {details.get('contrast_volume_ml', 'N/A')}mL @ {details.get('contrast_rate_ml_s', 'N/A')}mL/s")
        print(f"  │ Phases:        {details.get('phases', 'N/A')}")
        print(f"  │ Oral Prep:     {details.get('oral_prep', 'None')}")
        print(f"  │ Slice:         {details.get('slice_thickness_mm', 'N/A')}mm")
        print(f"  │ Recon:         {details.get('reconstruction', 'N/A')}")
        if details.get("special_instructions"):
            print(f"  │ Instructions:  {details.get('special_instructions', '')[:80]}")
    print("  └" + "─" * 40)
    
    # ── Protocol Steps (for MRI) ──
    steps = draft.get("protocol_steps", [])
    if steps:
        print("\n  ┌─── SEQUENCE STEPS ───")
        for s in steps:
            print(f"  │ {s.get('step_order', '?')}. {s.get('sequence_name', 'N/A'):30s}  {s.get('timing_description', '')}")
        print("  └" + "─" * 40)
    
    # ── Safety Profile ──
    safety = draft.get("safety_profile", {})
    if safety:
        overall = safety.get("overall_status", "unknown").upper()
        status_icon = "🟢" if overall == "CLEAR" else "🟡" if overall == "WARNINGS" else "🔴"
        
        print(f"\n  ┌─── SAFETY PROFILE ───")
        print(f"  │ Overall:        {status_icon} {overall}")
        print(f"  │ Data Source:     {safety.get('data_source', 'N/A')}")
        print(f"  │ Premed Required: {safety.get('premedication_required', False)}")
        if safety.get("premedication_text"):
            print(f"  │ Premed:         {safety.get('premedication_text', '')[:70]}...")
        if safety.get("substitute_protocol_id"):
            print(f"  │ Substitute:     {safety.get('substitute_protocol_id')}")
        
        flags = safety.get("safety_flags", [])
        if flags:
            print(f"  │")
            print(f"  │ Safety Flags ({len(flags)}):")
            for f in flags:
                icon = "🔴" if f.get("triggered") else "🟢"
                sev = f.get("severity", "").upper()
                msg = f.get("message", "")[:75]
                print(f"  │   {icon} [{sev:9s}] {msg}")
        
        labs = safety.get("lab_checks", [])
        if labs:
            print(f"  │")
            print(f"  │ Lab Checks ({len(labs)}):")
            for l in labs:
                icon = "🟢" if l.get("is_met") else "🔴"
                val = l.get("patient_value", "N/A")
                op = l.get("required_operator", "?")
                thresh = l.get("required_value", "?")
                print(f"  │   {icon} {l.get('lab_name', '?'):12s} {val} (need {op} {thresh})")
        
        meds = safety.get("med_holds", [])
        active_holds = [m for m in meds if m.get("patient_is_taking")]
        if active_holds:
            print(f"  │")
            print(f"  │ Active Medication Holds ({len(active_holds)}):")
            for m in active_holds:
                hold = m.get("hold_hours_before", "?")
                adj = m.get("adjusted_hold_hours")
                hold_str = f"{hold}h" if not adj else f"{adj}h (renal-adjusted from {hold}h)"
                resume = m.get("resume_hours_after", "?")
                print(f"  │   ⏸️  {m.get('medication_name', '?'):30s} Hold: {hold_str}, Resume: {resume}h after")
                if m.get("bridging_required"):
                    print(f"  │       🔄 Bridging: {m.get('bridging_protocol', 'N/A')[:60]}")
        
        print("  └" + "─" * 40)


if __name__ == "__main__":
    print("╔═══════════════════════════════════════════════════════╗")
    print("║   ACR-AC-RAG Protocoling Assistant — Live E2E Demo   ║")
    print("╚═══════════════════════════════════════════════════════╝")
    
    run_test("TEST 1: RLQ Pain — 28F, Omnipaque Allergy, on Eliquis, Pregnancy Risk", scenario_1)
    run_test("TEST 2: Acute Stroke — 72M, eGFR 28, on Warfarin, INR 2.8", scenario_2)
    run_test("TEST 3: Suspected PE — 45M, Post-Op, D-dimer 4200", scenario_3)
    
    print("\n" + "═" * 70)
    print("  ✅ ALL E2E TESTS COMPLETE")
    print("═" * 70)
