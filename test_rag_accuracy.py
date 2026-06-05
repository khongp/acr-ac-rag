import os
import sys
import json
from datetime import date, timedelta
import pytest

# Configure UTF-8 encoding for stdout on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Set device to CPU to avoid CUDA conflicts
os.environ["CUDA_VISIBLE_DEVICES"] = ""

from rag_engine import query_acr_guidelines
from protocol_mapper import get_draft_protocol
from fhir_converter import extract_scenario_from_bundle
from safety_engine import get_hard_contraindication_triggers, SafetyProfile as SP, SafetyFlag, LabCheckResult

def build_fhir_bundle(text: str, constraints: dict, expected_modality: str) -> dict:
    """Helper to dynamically generate a FHIR Bundle based on patient constraints."""
    entries = []
    
    # 1. Patient Resource
    age = constraints.get("age", 40)
    sex = constraints.get("sex", "male").lower()
    birth_date = (date.today() - timedelta(days=int(age * 365.25))).isoformat()
    
    entries.append({
        "resource": {
            "resourceType": "Patient",
            "id": "patient-1",
            "gender": sex,
            "birthDate": birth_date
        }
    })
    
    # 2. Condition Resource (Clinical History)
    entries.append({
        "resource": {
            "resourceType": "Condition",
            "id": "cond-1",
            "code": {
                "text": text
            }
        }
    })
    
    # 3. ServiceRequest Resource (Ordered Procedure)
    if expected_modality and expected_modality.lower() != "none":
        entries.append({
            "resource": {
                "resourceType": "ServiceRequest",
                "id": "req-1",
                "status": "draft",
                "intent": "proposal",
                "code": {
                    "concept": {
                        "text": expected_modality
                    }
                }
            }
        })
    
    # 3. Observations: Weight
    if "weight" in constraints:
        entries.append({
            "resource": {
                "resourceType": "Observation",
                "id": "obs-weight",
                "status": "final",
                "code": {
                    "coding": [{"system": "http://loinc.org", "code": "29463-7", "display": "Body weight"}]
                },
                "valueQuantity": {
                    "value": float(constraints["weight"]),
                    "unit": "kg"
                }
            }
        })
        
    # 4. Observations: eGFR
    if "egfr" in constraints:
        entries.append({
            "resource": {
                "resourceType": "Observation",
                "id": "obs-egfr",
                "status": "final",
                "code": {
                    "coding": [{"system": "http://loinc.org", "code": "33914-3", "display": "eGFR"}]
                },
                "valueQuantity": {
                    "value": float(constraints["egfr"]),
                    "unit": "mL/min/1.73m2"
                },
                "effectiveDateTime": date.today().isoformat()
            }
        })

    # 5. Observations: INR
    if "inr" in constraints:
        entries.append({
            "resource": {
                "resourceType": "Observation",
                "id": "obs-inr",
                "status": "final",
                "code": {
                    "coding": [{"system": "http://loinc.org", "code": "34714-6", "display": "INR"}]
                },
                "valueQuantity": {
                    "value": float(constraints["inr"])
                },
                "effectiveDateTime": date.today().isoformat()
            }
        })
        
    # 6. Observations: HCG (Pregnancy test)
    if constraints.get("pregnancy") is True:
        entries.append({
            "resource": {
                "resourceType": "Observation",
                "id": "obs-hcg",
                "status": "final",
                "code": {
                    "coding": [{"system": "http://loinc.org", "code": "19080-6", "display": "Beta-HCG"}]
                },
                "valueString": "positive",
                "effectiveDateTime": date.today().isoformat()
            }
        })
        
    # 7. AllergyIntolerance
    for allergy in constraints.get("allergies", []):
        entries.append({
            "resource": {
                "resourceType": "AllergyIntolerance",
                "code": {
                    "text": allergy
                }
            }
        })
        
    # 8. MedicationStatement
    for med in constraints.get("medications", []):
        entries.append({
            "resource": {
                "resourceType": "MedicationStatement",
                "medicationCodeableConcept": {
                    "text": med
                }
            }
        })
        
    # 9. Condition / Implants
    for implant in constraints.get("implants", []):
        entries.append({
            "resource": {
                "resourceType": "Condition",
                "code": {
                    "text": f"{implant} implant"
                }
            }
        })
        
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": entries
    }


def test_rag_accuracy_benchmark():
    """RAG Accuracy and Safety Regulation Benchmark Suite."""
    dataset_path = "data/golden_dataset.json"
    assert os.path.exists(dataset_path), f"Golden dataset not found at {dataset_path}"
    
    with open(dataset_path, "r", encoding="utf-8") as f:
        scenarios = json.load(f)
        
    passed_count = 0
    total_count = len(scenarios)
    results_report = []
    
    print(f"\nEvaluating {total_count} scenarios against Golden Dataset...")
    
    for item in scenarios:
        sid = item["id"]
        text = item["scenario_text"]
        expected_topic = item["expected_topic"]
        expected_modality = item["expected_modality"]
        constraints = item["patient_constraints"]
        expected_flags = item["expected_safety_flags"]
        expected_alts = item["expected_alternatives"]
        
        # Build FHIR bundle containing the patient constraints and expected modality
        bundle = build_fhir_bundle(text, constraints, expected_modality)
        
        # Run local business logic directly
        scenario_str = extract_scenario_from_bundle(bundle)
        acr_result = query_acr_guidelines(text, bundle)
        draft = get_draft_protocol(acr_result, bundle, "skyridge")
        draft_dict = draft.to_dict()
        
        status = "success"
        alternative_recommendation = None
        
        # Closed-loop: if hard contraindications found, re-query for alternatives
        if draft.safety_profile and draft.status in ("matched", "fuzzy_matched"):
            sp = SP(data_source=draft.safety_profile.get("data_source", "synthetic"))
            sp.safety_flags = [SafetyFlag(**f) for f in draft.safety_profile.get("safety_flags", [])]
            sp.lab_checks = [LabCheckResult(**lc) for lc in draft.safety_profile.get("lab_checks", [])]
            hard_triggers = get_hard_contraindication_triggers(sp)
            
            if hard_triggers:
                contraindications = [t["message"] for t in hard_triggers]
                alt_query = f"{scenario_str} CONSTRAINT: The following are contraindicated: {'; '.join(contraindications)}. Recommend only non-contrast or alternative modality options."
                alt_result = query_acr_guidelines(alt_query, bundle)
                status = "success_with_safety_requery"
                alternative_recommendation = alt_result.get("recommendation", "")
        
        # --- Check 1: Topic Matching (Top-1 Match Rate) ---
        matched_topic = draft_dict.get("acr_scenario") or ""
        acr_sources = acr_result.get("sources") or []
        
        source_topics = [src.get("metadata", {}).get("topic", "") for src in acr_sources]
        
        topic_matched = False
        if expected_topic.lower() in matched_topic.lower() or matched_topic.lower() in expected_topic.lower():
            topic_matched = True
        elif any(expected_topic.lower() in t.lower() or t.lower() in expected_topic.lower() for t in source_topics):
            topic_matched = True
            
        # --- Check 2: Safety Flags ---
        safety_profile = draft_dict.get("safety_profile") or {}
        triggered_flags = safety_profile.get("safety_flags") or []
        med_holds = safety_profile.get("med_holds") or []
        
        flags_matched = True
        missing_flags = []
        for ef in expected_flags:
            found = False
            if ef == "egfr_alert":
                found = any(f.get("rule_type") == "egfr_check" for f in triggered_flags)
            elif ef == "contrast_allergy":
                found = any(f.get("rule_type") == "allergy_check" for f in triggered_flags)
            elif ef == "pregnancy_alert":
                found = any(f.get("rule_type") == "pregnancy_check" for f in triggered_flags)
            elif ef == "pacemaker_stop":
                found = any(f.get("rule_type") == "mri_safety_pacemaker" for f in triggered_flags)
            elif ef == "pediatric_dosing":
                found = any(f.get("rule_type") == "pediatric_dosing" for f in triggered_flags)
            elif ef == "med_hold":
                found = len(med_holds) > 0 or any("hold" in f.get("message", "").lower() for f in triggered_flags)
            elif ef == "inr_alert":
                found = any(l.get("lab_name") == "INR" and not l.get("is_met") for l in safety_profile.get("lab_checks", []))
                
            if not found:
                flags_matched = False
                missing_flags.append(ef)
                
        # --- Check 3: Closed-loop Alternatives ---
        alts_matched = True
        if expected_alts:
            if status != "success_with_safety_requery":
                alts_matched = False
            else:
                found_alt = False
                for alt in expected_alts:
                    if alt.lower() in (alternative_recommendation or "").lower() or (alternative_recommendation or "").lower() in alt.lower():
                        found_alt = True
                        break
                if not found_alt:
                    # Also look in recommendation string
                    for alt in expected_alts:
                        if alt.lower() in acr_result.get("recommendation", "").lower():
                            found_alt = True
                            break
                if not found_alt:
                    alts_matched = False
                    
        # Overall Scenario Pass Status
        passed = topic_matched and flags_matched and alts_matched
        if passed:
            passed_count += 1
            
        results_report.append({
            "id": sid,
            "topic_expected": expected_topic,
            "topic_matched": matched_topic if matched_topic else (source_topics[0] if source_topics else "N/A"),
            "topic_pass": topic_matched,
            "flags_expected": expected_flags,
            "flags_missing": missing_flags,
            "flags_pass": flags_matched,
            "alts_expected": expected_alts,
            "alts_pass": alts_matched,
            "overall_pass": passed
        })
        
        status_char = "[PASS]" if passed else "[FAIL]"
        print(f"  {status_char} Scenario {sid}: TopicMatched={topic_matched}, FlagsMatched={flags_matched}, AltsMatched={alts_matched}")
        if not passed:
            print(f"    - Query: {text}")
            print(f"    - Topic Expected: {expected_topic} | Matched: {matched_topic or source_topics}")
            if missing_flags:
                print(f"    - Missing Flags: {missing_flags}")
            if not alts_matched:
                print(f"    - Expected Alts: {expected_alts} | Received Status: {status} | AltRec: {alternative_recommendation}")
                
    pass_rate = (passed_count / total_count) * 100
    print("\n" + "="*80)
    print(f" RAG & PROTOCOL ACCURACY BENCHMARK SUMMARY")
    print("="*80)
    print(f" Total Evaluated: {total_count}")
    print(f" Total Passed:    {passed_count}")
    print(f" Pass Rate:       {pass_rate:.1f}%")
    print("="*80)
    
    # Save a detailed report artifact in JSON format
    report_path = "data/eval/baseline_accuracy_report.json"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as rf:
        json.dump({
            "pass_rate": pass_rate,
            "total_count": total_count,
            "passed_count": passed_count,
            "details": results_report
        }, rf, indent=2)
    print(f"Saved accuracy evaluation report to {report_path}")
    
    # Assert targeting >90% pass rate
    assert pass_rate >= 90.0, f"RAG accuracy score {pass_rate:.1f}% is below 90% threshold!"

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
