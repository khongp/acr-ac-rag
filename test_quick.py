"""Quick debug: Test a single scenario against the live API."""
import sys
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

import requests
import json

scenario = (
    "28 year old female with acute right lower quadrant pain, nausea, fever. "
    "Allergy to Omnipaque. eGFR 62. On Eliquis 5mg BID. Pregnancy test pending."
)

print(f"Sending: {scenario}\n")
r = requests.post("http://localhost:8000/v1/protocol", json={"text": scenario}, timeout=120)
d = r.json()

print(f"HTTP Status: {r.status_code}")
print(f"API Status:  {d.get('status')}")
print(f"Scenario:    {d.get('extracted_scenario')}")
print()

# FHIR Resources
bundle = d.get("fhir_bundle", {})
entries = bundle.get("entry", [])
for e in entries:
    res = e.get("resource", {})
    rtype = res.get("resourceType")
    if rtype == "Patient":
        print(f"  [Patient] gender={res.get('gender')} birthDate={res.get('birthDate')}")
    elif rtype == "Condition":
        print(f"  [Condition] {res.get('code',{}).get('text','?')}")
    elif rtype == "Observation":
        val = res.get("valueQuantity", {}).get("value", res.get("valueString", "?"))
        print(f"  [Observation] {res.get('code',{}).get('text','?')} = {val}")
    elif rtype == "AllergyIntolerance":
        print(f"  [Allergy] {res.get('code',{}).get('text','?')}")
    elif rtype == "MedicationStatement":
        med = res.get("medication", {}).get("concept", {}).get("text", "?")
        print(f"  [Medication] {med}")

print()

# ACR Recommendation (truncated)
rec = d.get("acr_recommendation", "")
print("--- ACR RECOMMENDATION ---")
for line in rec.split("\n")[:15]:
    print(f"  {line}")
print()

# Draft Protocol
dp = d.get("draft_protocol", {})
if dp:
    print("--- DRAFT PROTOCOL ---")
    print(f"  Match:      {dp.get('status')}")
    print(f"  Confidence: {dp.get('confidence_score', 0):.0%}")
    print(f"  Protocol:   {dp.get('protocol_name')}")
    print(f"  Type:       {dp.get('protocol_type')}")
    print(f"  Method:     {dp.get('mapping_method')}")
    
    details = dp.get("protocol_details", {})
    if details.get("contrast_type"):
        print(f"\n  Contrast:   {details.get('contrast_type')} — {details.get('contrast_agent')}")
        print(f"  Volume:     {details.get('contrast_volume_ml')}mL @ {details.get('contrast_rate_ml_s')}mL/s")
        print(f"  Phases:     {details.get('phases')}")
        print(f"  Oral Prep:  {details.get('oral_prep')}")
        print(f"  Slice:      {details.get('slice_thickness_mm')}mm")
    
    safety = dp.get("safety_profile", {})
    if safety:
        print(f"\n--- SAFETY ---")
        print(f"  Overall:    {safety.get('overall_status', '?').upper()}")
        print(f"  Premed:     {safety.get('premedication_required')}")
        if safety.get("premedication_text"):
            print(f"  Premed Rx:  {safety['premedication_text'][:80]}...")
        if safety.get("substitute_protocol_id"):
            print(f"  Alt Proto:  {safety['substitute_protocol_id']}")
        
        flags = safety.get("safety_flags", [])
        print(f"\n  Flags ({len(flags)}):")
        for f in flags:
            icon = "🔴" if f.get("triggered") else "🟢"
            print(f"    {icon} [{f.get('severity','?'):9s}] {f.get('message','')[:80]}")
else:
    print("No draft protocol")
    if d.get("protocol_error"):
        print(f"Error: {d['protocol_error']}")
