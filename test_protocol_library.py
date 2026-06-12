"""
Protocol Library — Verification Tests
=======================================
Tests the full pipeline: Protocol DB → Safety Engine → Protocol Mapper
Runs without the RAG engine or LLM (pure database + rule evaluation tests).
"""

import os
import json
import sys

# Force UTF-8 on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from protocol_db import (
    initialize_db, get_connection,
    lookup_protocol_by_acr, get_contrast_rules, get_ir_protocol_details,
    get_protocol_steps, list_protocols, list_ir_protocols, search_protocols_fulltext,
)
from safety_engine import (
    extract_patient_safety_data, evaluate_safety,
    evaluate_contrast_rules, evaluate_ir_lab_thresholds, evaluate_ir_med_holds,
)

DB_PATH = os.path.join("data", "protocols", "skyridge_protocols.db")


def test_db_queries():
    """Test that all seed data is queryable."""
    print("\n═══ TEST 1: Database Queries ═══")
    
    # List all imaging protocols
    protocols = list_protocols("skyridge", db_path=DB_PATH)
    print(f"  Imaging protocols: {len(protocols)}")
    for p in protocols:
        print(f"    [{p['modality']}] {p['name']}")
    assert len(protocols) == 8, f"Expected 8 imaging protocols, got {len(protocols)}"
    
    # List all IR protocols
    ir_protocols = list_ir_protocols("skyridge", db_path=DB_PATH)
    print(f"  IR protocols: {len(ir_protocols)}")
    for p in ir_protocols:
        print(f"    [{p['procedure_category']}] {p['name']} (SIR risk: {p['sir_bleeding_risk']})")
    assert len(ir_protocols) == 4, f"Expected 4 IR protocols, got {len(ir_protocols)}"
    
    # Test ACR bridge lookup
    matches = lookup_protocol_by_acr(
        acr_procedure_text="CT abdomen and pelvis with IV contrast",
        institution_id="skyridge",
        db_path=DB_PATH
    )
    print(f"  ACR lookup 'CT abdomen and pelvis with IV contrast': {len(matches)} match(es)")
    assert len(matches) >= 1, "Expected at least 1 match for CT abd/pelvis"
    print(f"    → {matches[0].get('protocol_name')} (confidence: {matches[0].get('match_confidence')})")
    
    # Test protocol steps (MRI Brain)
    steps = get_protocol_steps("sr_mri_brain_wwoc", db_path=DB_PATH)
    print(f"  MRI Brain protocol steps: {len(steps)}")
    assert len(steps) == 9, f"Expected 9 MRI steps, got {len(steps)}"
    
    # Test IR protocol details
    ir_detail = get_ir_protocol_details("sr_ir_liver_biopsy", db_path=DB_PATH)
    print(f"  Liver Biopsy lab thresholds: {len(ir_detail.get('lab_thresholds', []))}")
    print(f"  Liver Biopsy med holds: {len(ir_detail.get('med_holds', []))}")
    assert len(ir_detail['lab_thresholds']) == 4
    assert len(ir_detail['med_holds']) == 9
    
    # Test full-text search
    search_results = search_protocols_fulltext("skyridge", "brain", db_path=DB_PATH)
    print(f"  Search 'brain': {len(search_results)} result(s)")
    
    print("  ✅ All DB query tests passed")


def test_safety_engine_egfr():
    """Test eGFR safety rule evaluation."""
    print("\n═══ TEST 2: Safety Engine — eGFR Check ═══")
    
    rules = get_contrast_rules("sr_ct_abd_pelvis_appendix", db_path=DB_PATH)
    egfr_rules = [r for r in rules if r['rule_type'] == 'egfr_check']
    
    # Patient with low eGFR — should trigger
    patient_low = {"egfr": {"value": 25.0, "date": "2026-05-10"}, "allergies": [], "medications": []}
    flags = evaluate_contrast_rules(egfr_rules, patient_low)
    triggered = [f for f in flags if f.triggered]
    print(f"  Patient eGFR=25: {len(triggered)} flag(s) triggered")
    assert len(triggered) == 1, "eGFR=25 should trigger 1 flag"
    print(f"    → {triggered[0].message}")
    
    # Patient with normal eGFR — should not trigger
    patient_normal = {"egfr": {"value": 85.0, "date": "2026-05-10"}, "allergies": [], "medications": []}
    flags = evaluate_contrast_rules(egfr_rules, patient_normal)
    triggered = [f for f in flags if f.triggered]
    print(f"  Patient eGFR=85: {len(triggered)} flag(s) triggered")
    assert len(triggered) == 0, "eGFR=85 should not trigger"
    
    # Patient with no eGFR data
    patient_none = {"egfr": None, "allergies": [], "medications": []}
    flags = evaluate_contrast_rules(egfr_rules, patient_none)
    triggered = [f for f in flags if f.triggered]
    print(f"  Patient no eGFR: {len(triggered)} flag(s) triggered")
    assert len(triggered) == 0, "No eGFR data should not trigger (no data != low)"
    
    print("  ✅ eGFR tests passed")


def test_safety_engine_allergy():
    """Test contrast allergy safety rule evaluation."""
    print("\n═══ TEST 3: Safety Engine — Allergy Check ═══")
    
    rules = get_contrast_rules("sr_ct_abd_pelvis_appendix", db_path=DB_PATH)
    allergy_rules = [r for r in rules if r['rule_type'] == 'allergy_check']
    
    # Patient with Omnipaque allergy
    patient_allergic = {"egfr": None, "allergies": ["Omnipaque"], "medications": []}
    flags = evaluate_contrast_rules(allergy_rules, patient_allergic)
    triggered = [f for f in flags if f.triggered]
    print(f"  Patient allergic to Omnipaque: {len(triggered)} flag(s)")
    assert len(triggered) == 1
    assert triggered[0].action == "require_premedication"
    print(f"    → Action: {triggered[0].action}")
    print(f"    → Premed: {triggered[0].details.get('premedication_text', 'N/A')[:60]}...")
    
    # Patient with no allergies
    patient_clean = {"egfr": None, "allergies": [], "medications": []}
    flags = evaluate_contrast_rules(allergy_rules, patient_clean)
    triggered = [f for f in flags if f.triggered]
    print(f"  Patient no allergies: {len(triggered)} flag(s)")
    assert len(triggered) == 0
    
    print("  ✅ Allergy tests passed")


def test_safety_engine_pregnancy():
    """Test pregnancy safety rule evaluation."""
    print("\n═══ TEST 4: Safety Engine — Pregnancy Check ═══")
    
    rules = get_contrast_rules("sr_ct_abd_pelvis_appendix", db_path=DB_PATH)
    preg_rules = [r for r in rules if r['rule_type'] == 'pregnancy_check']
    
    # Female, 28, no HCG — should trigger
    patient_female = {
        "sex": "female", "age": 28,
        "egfr": None, "hcg": None, "allergies": [], "medications": []
    }
    flags = evaluate_contrast_rules(preg_rules, patient_female)
    triggered = [f for f in flags if f.triggered]
    print(f"  Female age 28, no HCG: {len(triggered)} flag(s)")
    assert len(triggered) == 1
    assert triggered[0].severity == "hard_stop"
    print(f"    → Severity: {triggered[0].severity}")
    
    # Female, 28, HCG negative — should NOT trigger
    patient_neg = {
        "sex": "female", "age": 28,
        "egfr": None, "hcg": {"value": "negative", "date": "2026-05-14"},
        "allergies": [], "medications": []
    }
    flags = evaluate_contrast_rules(preg_rules, patient_neg)
    triggered = [f for f in flags if f.triggered]
    print(f"  Female age 28, HCG negative: {len(triggered)} flag(s)")
    assert len(triggered) == 0
    
    # Male patient — should NOT trigger
    patient_male = {
        "sex": "male", "age": 35,
        "egfr": None, "hcg": None, "allergies": [], "medications": []
    }
    flags = evaluate_contrast_rules(preg_rules, patient_male)
    triggered = [f for f in flags if f.triggered]
    print(f"  Male age 35: {len(triggered)} flag(s)")
    assert len(triggered) == 0
    
    print("  ✅ Pregnancy tests passed")


def test_ir_lab_thresholds():
    """Test IR lab threshold evaluation."""
    print("\n═══ TEST 5: IR Lab Thresholds — Liver Biopsy ═══")
    
    from datetime import date
    today = date.today().isoformat()
    
    ir_detail = get_ir_protocol_details("sr_ir_liver_biopsy", db_path=DB_PATH)
    thresholds = ir_detail['lab_thresholds']
    
    # Patient with good labs (using today's date to avoid staleness)
    patient_good = {
        "inr": {"value": 1.1, "date": today},
        "platelets": {"value": 200000, "date": today},
        "hgb": {"value": 13.5, "date": today},
        "fibrinogen": {"value": 350, "date": today},
    }
    results = evaluate_ir_lab_thresholds(thresholds, patient_good)
    failed = [r for r in results if not r.is_met]
    print(f"  Good labs (INR 1.1, Plt 200K): {len(failed)} failed")
    assert len(failed) == 0, f"Expected 0 failures, got {len(failed)}: {[(r.lab_name, r.patient_value, r.is_stale) for r in failed]}"
    
    # Patient with bad INR and low platelets
    patient_bad = {
        "inr": {"value": 2.5, "date": today},
        "platelets": {"value": 30000, "date": today},
        "hgb": {"value": 13.5, "date": today},
        "fibrinogen": {"value": 350, "date": today},
    }
    results = evaluate_ir_lab_thresholds(thresholds, patient_bad)
    failed = [r for r in results if not r.is_met]
    print(f"  Bad labs (INR 2.5, Plt 30K): {len(failed)} failed")
    assert len(failed) >= 2
    for r in failed:
        print(f"    ❌ {r.lab_name}: {r.patient_value} (need {r.required_operator} {r.required_value}) — {r.action_if_not_met}")
    
    print("  ✅ IR lab threshold tests passed")


def test_ir_med_holds():
    """Test IR medication hold evaluation."""
    print("\n═══ TEST 6: IR Med Holds — Liver Biopsy ═══")
    
    ir_detail = get_ir_protocol_details("sr_ir_liver_biopsy", db_path=DB_PATH)
    med_holds = ir_detail['med_holds']
    
    # Patient on Eliquis and Plavix
    patient = {
        "medications": ["Apixaban 5mg BID", "Clopidogrel 75mg daily"],
        "egfr": {"value": 65, "date": "2026-05-14"},
    }
    alerts = evaluate_ir_med_holds(med_holds, patient)
    taking = [a for a in alerts if a.patient_is_taking]
    print(f"  Patient on Eliquis + Plavix: {len(taking)} med hold(s) detected")
    for a in taking:
        hold_time = f"{a.hold_hours_before}h"
        if a.adjusted_hold_hours:
            hold_time = f"{a.adjusted_hold_hours}h (renal-adjusted from {a.hold_hours_before}h)"
        print(f"    ⏸️  {a.medication_name}: hold {hold_time} before, resume {a.resume_hours_after}h after")
    assert len(taking) >= 2
    
    # Patient on Eliquis with low eGFR — should adjust hold time
    patient_renal = {
        "medications": ["Eliquis"],
        "egfr": {"value": 20, "date": "2026-05-14"},
    }
    alerts = evaluate_ir_med_holds(med_holds, patient_renal)
    eliquis_alert = next(a for a in alerts if a.patient_is_taking and "Apixaban" in a.medication_name)
    print(f"\n  Patient on Eliquis with eGFR=20:")
    print(f"    Standard hold: {eliquis_alert.hold_hours_before}h")
    print(f"    Renal-adjusted: {eliquis_alert.adjusted_hold_hours}h")
    print(f"    Was adjusted: {eliquis_alert.renal_adjusted}")
    assert eliquis_alert.renal_adjusted == True
    assert eliquis_alert.adjusted_hold_hours == 72
    
    print("  ✅ IR med hold tests passed")


def test_full_safety_evaluation():
    """Test the complete safety evaluation for a complex scenario."""
    print("\n═══ TEST 7: Full Safety Evaluation — Complex Scenario ═══")
    print("  Scenario: 34F with RLQ pain, allergy to Omnipaque, eGFR 25, on Eliquis")
    
    # Get CT appendicitis protocol with all rules
    matches = lookup_protocol_by_acr(
        acr_procedure_text="CT abdomen and pelvis with IV contrast",
        institution_id="skyridge",
        db_path=DB_PATH
    )
    protocol_data = matches[0]
    
    # Simulated patient
    patient_data = {
        "sex": "female",
        "age": 34,
        "egfr": {"value": 25.0, "date": "2026-05-10"},
        "inr": None,
        "platelets": None,
        "hgb": None,
        "fibrinogen": None,
        "hcg": None,
        "allergies": ["Omnipaque"],
        "medications": ["Eliquis 5mg BID"],
    }
    
    profile = evaluate_safety(protocol_data, patient_data, data_source="synthetic")
    
    print(f"\n  Overall Status: {profile.overall_status}")
    print(f"  Safety Flags: {len(profile.safety_flags)}")
    for f in profile.safety_flags:
        status = "🔴" if f.triggered else "🟢"
        print(f"    {status} [{f.rule_type}] {f.message[:80]}")
    print(f"  Premedication Required: {profile.premedication_required}")
    if profile.premedication_text:
        print(f"    → {profile.premedication_text[:80]}...")
    print(f"  Substitute Protocol: {profile.substitute_protocol_id}")
    
    assert profile.overall_status == "hard_stop", f"Expected hard_stop, got {profile.overall_status}"
    triggered_types = {f.rule_type for f in profile.safety_flags if f.triggered}
    assert "egfr_check" in triggered_types, "eGFR check should have triggered"
    assert "allergy_check" in triggered_types, "Allergy check should have triggered"
    assert "pregnancy_check" in triggered_types, "Pregnancy check should have triggered (no HCG on file)"
    assert profile.premedication_required == True
    
    print("\n  ✅ Full safety evaluation test passed — all 3 rules correctly triggered")


def test_fhir_extraction():
    """Test extracting safety data from a FHIR Bundle."""
    print("\n═══ TEST 8: FHIR Bundle → Safety Data Extraction ═══")
    
    # Simulate a FHIR bundle with safety-relevant resources
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"resource": {
                "resourceType": "Patient",
                "id": "patient-1",
                "gender": "female",
                "birthDate": "1992-03-15"
            }},
            {"resource": {
                "resourceType": "Observation",
                "status": "final",
                "code": {"coding": [{"display": "Glomerular filtration rate", "code": "69405-9"}], "text": "eGFR"},
                "valueQuantity": {"value": 28.0, "unit": "mL/min"},
                "effectiveDateTime": "2026-05-10"
            }},
            {"resource": {
                "resourceType": "AllergyIntolerance",
                "code": {"coding": [{"display": "Omnipaque"}], "text": "Omnipaque"},
                "patient": {"reference": "Patient/patient-1"}
            }},
            {"resource": {
                "resourceType": "MedicationStatement",
                "status": "active",
                "medicationCodeableConcept": {"coding": [{"display": "Apixaban"}], "text": "Apixaban (Eliquis)"},
                "subject": {"reference": "Patient/patient-1"}
            }}
        ]
    }
    
    data = extract_patient_safety_data(bundle)
    
    print(f"  Sex: {data['sex']}")
    print(f"  Age: {data['age']}")
    print(f"  eGFR: {data['egfr']}")
    print(f"  Allergies: {data['allergies']}")
    print(f"  Medications: {data['medications']}")
    
    assert data['sex'] == 'female'
    assert data['age'] is not None and 33 <= data['age'] <= 35  # ~34 years old
    assert data['egfr']['value'] == 28.0
    assert 'Omnipaque' in data['allergies']
    assert any('Apixaban' in m or 'Eliquis' in m for m in data['medications'])
    
    print("  ✅ FHIR extraction test passed")


if __name__ == "__main__":
    print("╔══════════════════════════════════════════╗")
    print("║  Protocol Library — Verification Suite   ║")
    print("╚══════════════════════════════════════════╝")
    
    test_db_queries()
    test_safety_engine_egfr()
    test_safety_engine_allergy()
    test_safety_engine_pregnancy()
    test_ir_lab_thresholds()
    test_ir_med_holds()
    test_full_safety_evaluation()
    test_fhir_extraction()
    
    print("\n" + "═" * 50)
    print("  ✅ ALL 8 TESTS PASSED")
    print("═" * 50)
