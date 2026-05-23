import os
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_analyze():
    if "GOOGLE_API_KEY" not in os.environ:
        print("Skipping /v1/analyze test because GOOGLE_API_KEY is not set.")
        return
        
    print("Testing /v1/analyze...")
    response = client.post(
        "/v1/analyze",
        json={"text": "70yo male with thunderclap headache"}
    )
    print("Status:", response.status_code)
    if response.status_code == 200:
        data = response.json()
        print("Mock Bundle Keys:", data.get("mock_bundle_used").keys() if data.get("mock_bundle_used") else None)
        print("Extracted Scenario:", data.get("extracted_scenario"))
        print("Recommendation Length:", len(data.get("recommendation", "")))
    else:
        print("Error:", response.text)

def test_cds_hook():
    print("\nTesting /v1/cds-hook...")
    response = client.post(
        "/v1/cds-hook",
        json={
            "hook": "order-select",
            "context": {
                "userId": "Practitioner/123",
                "patientId": "1288992",
                "selections": ["ServiceRequest/draft-1"]
            }
        }
    )
    print("Status:", response.status_code)
    print("Response:", response.json())

if __name__ == "__main__":
    test_analyze()
    test_cds_hook()
