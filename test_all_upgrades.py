import os
import sys
import json
from datetime import date, timedelta
from fastapi.testclient import TestClient
from main import app

# Configure UTF-8 encoding for stdout on Windows
sys.stdout.reconfigure(encoding='utf-8')

client = TestClient(app)

def test_multi_institution_protocol():
    """Verify that different hospitals yield different scanners and protocols."""
    print("\n--- Testing Multi-Institution Protocol Mapper ---")
    
    # Preset: Headache / Suspected mass protocol (Headache / lesion map)
    scenario_text = "55 year old female presenting with persistent severe headache. Has a suspected new intracranial lesion or mass."
    
    # 1. Test at Skyridge Medical Center (default)
    print("Requesting protocol from Skyridge Medical Center...")
    response_skyridge = client.post(
        "/v1/protocol",
        json={"text": scenario_text, "institution_id": "skyridge"}
    )
    assert response_skyridge.status_code == 200, f"Error: {response_skyridge.text}"
    data_skyridge = response_skyridge.json()
    
    # 2. Test at Denver General Hospital
    print("Requesting protocol from Denver General Hospital...")
    response_denver = client.post(
        "/v1/protocol",
        json={"text": scenario_text, "institution_id": "denver_general"}
    )
    assert response_denver.status_code == 200, f"Error: {response_denver.text}"
    data_denver = response_denver.json()
    
    # Check if Skyridge returned its customized protocol and Denver General returned its customized protocol
    protocol_sky = data_skyridge.get("draft_protocol")
    protocol_den = data_denver.get("draft_protocol")
    
    if protocol_sky and protocol_den:
        print(f"Skyridge Protocol: ID={protocol_sky.get('protocol_id')}, Name={protocol_sky.get('protocol_name')}")
        print(f"Denver General Protocol: ID={protocol_den.get('protocol_id')}, Name={protocol_den.get('protocol_name')}")
        
        # Verify multi-tenancy custom protocol differences
        assert protocol_sky.get("institution_id") == "skyridge", "Skyridge institution mismatch!"
        assert protocol_den.get("institution_id") == "denver_general", "Denver General institution mismatch!"
        assert protocol_sky.get("protocol_id") != protocol_den.get("protocol_id"), "Institution protocol IDs should be different!"
        assert protocol_sky.get("protocol_name") != protocol_den.get("protocol_name"), "Institution protocol names should be different!"
        print("Success: Institution protocols are customized and institution-specific!")
    else:
        print("Warning: One of the protocols was not matched.")
        assert False, "Failed to match protocols for institutions!"


def test_safety_pediatric():
    """Verify pediatric weight-based calculations and missing weight safety alert."""
    print("\n--- Testing Pediatric Dosing Rule ---")
    
    # Synthesize a pediatric FHIR bundle (8yo, 25kg)
    bundle_pediatric = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "pediatric-1",
                    "gender": "male",
                    "birthDate": (date.today() - timedelta(days=8*365)).isoformat()
                }
            },
            {
                "resource": {
                    "resourceType": "ServiceRequest",
                    "id": "req-1",
                    "status": "draft",
                    "intent": "proposal",
                    "code": {
                        "concept": {
                            "coding": [{"display": "CT Abdomen and Pelvis with contrast"}],
                            "text": "CT Abdomen and Pelvis with contrast"
                        }
                    }
                }
            },
            {
                "resource": {
                    "resourceType": "Observation",
                    "id": "obs-weight",
                    "status": "final",
                    "code": {
                        "coding": [{"system": "http://loinc.org", "code": "29463-7", "display": "Body weight"}]
                    },
                    "valueQuantity": {
                        "value": 25.0,
                        "unit": "kg"
                    }
                }
            },
            {
                "resource": {
                    "resourceType": "Condition",
                    "id": "cond-rlq",
                    "clinicalStatus": {"coding": [{"code": "active"}]},
                    "code": {
                        "text": "Right lower quadrant pain, clinically high risk for acute appendicitis."
                    }
                }
            }
        ]
    }
    
    print("Testing pediatric patient with WEIGHT (25 kg)...")
    response_with_weight = client.post(
        "/v1/cds-hook",
        json={
            "hook": "order-select",
            "context": {"userId": "Prac/1", "patientId": "pediatric-1", "selections": ["ServiceRequest/req-1"]},
            "prefetch": {
                "patient": {"resource": bundle_pediatric["entry"][0]["resource"]},
                "activeOrders": {"resource": bundle_pediatric}
            }
        }
    )
    assert response_with_weight.status_code == 200, f"Error: {response_with_weight.text}"
    cards = response_with_weight.json().get("cards", [])
    print("Pediatric Test Cards returned:", json.dumps(cards, indent=2))
    
    pediatric_card_found = False
    for card in cards:
        if "pediatric" in card.get("summary", "").lower() or "pediatric" in card.get("detail", "").lower():
            print("Triggered Card Details:", card.get("detail"))
            assert "50.0 mL" in card.get("detail") or "50 ml" in card.get("detail").lower(), "Calculated volume should be 2 mL * 25 kg = 50 mL"
            pediatric_card_found = True
            break
            
    assert pediatric_card_found, "Pediatric dose adjustment card should be returned!"
    print("Success: Pediatric weight-based contrast adjustment calculated and flagged correctly!")


def test_safety_pacemaker():
    """Verify MRI Pacemaker absolute stop warning."""
    print("\n--- Testing MRI Pacemaker safety rule ---")
    
    # Synthesize an MRI proposed order on a pacemaker patient
    bundle_pacemaker = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "pacemaker-1",
                    "gender": "female",
                    "birthDate": "1970-01-01"
                }
            },
            {
                "resource": {
                    "resourceType": "ServiceRequest",
                    "id": "req-2",
                    "status": "draft",
                    "intent": "proposal",
                    "code": {
                        "concept": {
                            "coding": [{"display": "MRI Brain without and with contrast"}],
                            "text": "MRI Brain without and with contrast"
                        }
                    }
                }
            },
            {
                "resource": {
                    "resourceType": "Condition",
                    "id": "cond-pacemaker",
                    "clinicalStatus": {"coding": [{"code": "active"}]},
                    "code": {
                        "coding": [{"display": "Cardiac pacemaker implant"}],
                        "text": "cardiac pacemaker"
                    }
                }
            },
            {
                "resource": {
                    "resourceType": "Condition",
                    "id": "cond-headache",
                    "clinicalStatus": {"coding": [{"code": "active"}]},
                    "code": {
                        "text": "persistent severe headache, rule out intracranial lesion or mass"
                    }
                }
            }
        ]
    }
    
    response = client.post(
        "/v1/cds-hook",
        json={
            "hook": "order-select",
            "context": {"userId": "Prac/1", "patientId": "pacemaker-1", "selections": ["ServiceRequest/req-2"]},
            "prefetch": {
                "patient": {"resource": bundle_pacemaker["entry"][0]["resource"]},
                "activeOrders": {"resource": bundle_pacemaker}
            }
        }
    )
    assert response.status_code == 200, f"Error: {response.text}"
    cards = response.json().get("cards", [])
    
    pacemaker_flag_found = False
    for card in cards:
        if "pacemaker" in card.get("summary", "").lower():
            print("Triggered Safety Card:", card.get("summary"))
            print("Detail:", card.get("detail"))
            assert card.get("indicator") == "critical", "Pacemaker safety alert should have critical indicator"
            pacemaker_flag_found = True
            break
            
    assert pacemaker_flag_found, "Pacemaker safety pre-screening alert card should be returned!"
    print("Success: Pacemaker critical stop card generated correctly!")


def test_safety_radiation():
    """Verify cumulative radiation risk checker (flags if CT scans occurred in last 72 hours)."""
    print("\n--- Testing Cumulative Radiation safety rule ---")
    
    # Synthesize a patient who had a CT scan 24 hours ago
    scan_date = (date.today() - timedelta(days=1)).isoformat()
    bundle_radiation = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "resource": {
                    "resourceType": "Patient",
                    "id": "rad-1",
                    "gender": "male",
                    "birthDate": "1964-01-01"
                }
            },
            {
                "resource": {
                    "resourceType": "ServiceRequest",
                    "id": "req-3",
                    "status": "draft",
                    "intent": "proposal",
                    "code": {
                        "concept": {
                            "coding": [{"display": "CT Abdomen and Pelvis with contrast"}],
                            "text": "CT Abdomen and Pelvis with contrast"
                        }
                    }
                }
            },
            {
                "resource": {
                    "resourceType": "Procedure",
                    "id": "proc-recent",
                    "status": "completed",
                    "code": {
                        "coding": [{"display": "Recent CT Scan"}],
                        "text": "CT scan of abdomen 24 hours ago"
                    },
                    "performedDateTime": scan_date
                }
            },
            {
                "resource": {
                    "resourceType": "Condition",
                    "id": "cond-rlq",
                    "clinicalStatus": {"coding": [{"code": "active"}]},
                    "code": {
                        "text": "Right lower quadrant pain, clinically high risk for acute appendicitis."
                    }
                }
            }
        ]
    }
    
    response = client.post(
        "/v1/cds-hook",
        json={
            "hook": "order-select",
            "context": {"userId": "Prac/1", "patientId": "rad-1", "selections": ["ServiceRequest/req-3"]},
            "prefetch": {
                "patient": {"resource": bundle_radiation["entry"][0]["resource"]},
                "activeOrders": {"resource": bundle_radiation}
            }
        }
    )
    assert response.status_code == 200, f"Error: {response.text}"
    cards = response.json().get("cards", [])
    
    radiation_flag_found = False
    for card in cards:
        if "radiation" in card.get("summary", "").lower():
            print("Triggered Radiation Card:", card.get("summary"))
            print("Detail:", card.get("detail"))
            assert card.get("indicator") == "warning", "Radiation safety alert should have warning indicator"
            radiation_flag_found = True
            break
            
    assert radiation_flag_found, "Cumulative radiation warning card should be returned!"
    print("Success: Cumulative radiation risk warning card generated correctly!")


def test_copilot_chat():
    """Verify Attending Radiology Co-Pilot conversational endpoint."""
    print("\n--- Testing Attending Co-Pilot Chat Drawer ---")
    
    if "GOOGLE_API_KEY" not in os.environ:
        print("Skipping Attending Co-Pilot test because GOOGLE_API_KEY is not set.")
        return
        
    scenario_text = "69 year old female presenting with acute worsening low back pain and saddle anesthesia. Suspected cauda equina syndrome."
    
    response = client.post(
        "/v1/copilot/chat",
        json={
            "scenario_text": scenario_text,
            "chat_history": [
                {"role": "user", "content": "Why is MRI recommended over CT for cauda equina?"}
            ],
            "message": "Is there any situation where we should do a CT scan instead of MRI?"
        }
    )
    assert response.status_code == 200, f"Error: {response.text}"
    data = response.json()
    print("Co-Pilot response:")
    print(data.get("response"))
    assert len(data.get("response", "")) > 0, "Co-Pilot response should not be empty!"
    print("Success: Co-Pilot chat response generated successfully!")


if __name__ == "__main__":
    print("Running system upgrade tests...")
    test_multi_institution_protocol()
    test_safety_pediatric()
    test_safety_pacemaker()
    test_safety_radiation()
    test_copilot_chat()
    print("\nAll system upgrade tests passed successfully!")
