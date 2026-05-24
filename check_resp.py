import os
import json
from main import app
from fastapi.testclient import TestClient

client = TestClient(app)

scenario_text = "72 year old male with sudden onset slurred speech and right hemiplegia. eGFR 28. On Warfarin therapy."

print("Calling /v1/protocol for Skyridge...")
resp_sky = client.post("/v1/protocol", json={"text": scenario_text, "institution_id": "skyridge"})
data_sky = resp_sky.json()

print("Status Code:", resp_sky.status_code)
print("Response JSON Keys:", list(data_sky.keys()))
print("Status:", data_sky.get("status"))
print("Extracted Scenario:", data_sky.get("extracted_scenario"))
print("ACR Recommendation text length:", len(data_sky.get("acr_recommendation", "")))
print("ACR Sources count:", len(data_sky.get("acr_sources", [])))
print("Draft Protocol:")
print(json.dumps(data_sky.get("draft_protocol"), indent=2))

# Also print what identifiers are extracted
from protocol_mapper import _extract_acr_identifiers
acr_result = {
    "recommendation": data_sky.get("acr_recommendation"),
    "sources": data_sky.get("acr_sources")
}
proc, scen = _extract_acr_identifiers(acr_result)
print(f"Extracted Identifiers: Proc='{proc}', Scen='{scen}'")
