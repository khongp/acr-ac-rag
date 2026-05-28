import os
import json
from pydantic import BaseModel, Field
from typing import Optional, List
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv

load_dotenv()

from fhir.resources.bundle import Bundle

# Simplified extraction models for LLM
class ExtractedPatient(BaseModel):
    age: Optional[int] = Field(None, description="Patient's age in years")
    gender: Optional[str] = Field(None, description="Patient's gender (male, female, other, unknown)")
    birth_date: Optional[str] = Field(None, description="Patient's date of birth in YYYY-MM-DD format, if mentioned")

class ExtractedCondition(BaseModel):
    description: str = Field(description="The primary clinical indication, symptomology, or established diagnosis")

class ExtractedServiceRequest(BaseModel):
    modality: Optional[str] = Field(None, description="The proposed or requested imaging modality (e.g., MRI Brain, CT Chest). Null if none requested.")

class ExtractedLabValue(BaseModel):
    """A laboratory result mentioned in the clinical scenario."""
    lab_name: str = Field(description="Name of the lab test (e.g., eGFR, INR, Platelets, Hemoglobin, Fibrinogen, HCG, Creatinine)")
    value: Optional[float] = Field(None, description="Numeric value of the lab result. Null if only qualitative (e.g., 'negative').")
    value_string: Optional[str] = Field(None, description="String value if qualitative (e.g., 'negative', 'positive'). Null if numeric.")
    unit: Optional[str] = Field(None, description="Unit of measurement (e.g., mL/min, mg/dL, K/uL)")

class ExtractedAllergy(BaseModel):
    """An allergy mentioned in the clinical scenario."""
    substance: str = Field(description="The substance the patient is allergic to (e.g., Omnipaque, iodinated contrast, Gadolinium, penicillin)")
    reaction: Optional[str] = Field(None, description="Type of reaction if mentioned (e.g., anaphylaxis, hives, rash)")

class ExtractedMedication(BaseModel):
    """A medication the patient is currently taking."""
    name: str = Field(description="Medication name (e.g., Eliquis, Apixaban, Warfarin, Plavix, Aspirin, Metformin)")
    dose: Optional[str] = Field(None, description="Dosage if mentioned (e.g., 5mg BID)")

class ClinicalExtraction(BaseModel):
    patient: ExtractedPatient
    conditions: List[ExtractedCondition]
    service_request: Optional[ExtractedServiceRequest] = None
    lab_values: List[ExtractedLabValue] = Field(default_factory=list, description="Any lab results mentioned in the scenario")
    allergies: List[ExtractedAllergy] = Field(default_factory=list, description="Any allergies mentioned in the scenario")
    medications: List[ExtractedMedication] = Field(default_factory=list, description="Any current medications mentioned in the scenario")
    patient_weight_kg: Optional[float] = Field(None, description="Patient's weight in kilograms (kg) if mentioned. If weight is mentioned in lbs, convert it to kg (lbs / 2.2).")
    recent_procedures: List[str] = Field(default_factory=list, description="Any past scans, procedures, or surgeries (especially CT, MRI, X-rays) along with timeframe if mentioned.")
    implants: List[str] = Field(default_factory=list, description="Any implants, pacemakers, cochlear implants, aneurysm clips, or claustrophobia mentioned.")

def get_extraction_llm():
    from llm_router import get_llm
    return get_llm(temperature=0.0)

def fallback_text_to_fhir_bundle(clinical_scenario: str) -> Bundle:
    """
    A regex-based backup parser that generates a standard FHIR bundle when the LLM is rate-limited or offline.
    """
    import re
    from datetime import date
    
    entries = []
    
    # 1. Age extraction
    age = None
    age_match = re.search(r"\b(\d{1,3})\s*(?:yo|year|yr|-year|years\s+old)\b", clinical_scenario, re.IGNORECASE)
    if age_match:
        age = int(age_match.group(1))
        
    # 2. Gender extraction
    gender = "unknown"
    if re.search(r"\b(male|man|m|gentleman|boy)\b", clinical_scenario, re.IGNORECASE):
        gender = "male"
    elif re.search(r"\b(female|woman|f|lady|girl)\b", clinical_scenario, re.IGNORECASE):
        gender = "female"
        
    # Patient Resource
    patient_data = {
        "id": "patient-1",
        "resourceType": "Patient",
        "gender": gender
    }
    if age:
        patient_data["birthDate"] = f"{date.today().year - age}-01-01"
    
    entries.append({"resource": patient_data})
    
    # 3. Clinical condition (Indications)
    condition_data = {
        "id": "condition-1",
        "resourceType": "Condition",
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
        "subject": {"reference": "Patient/patient-1"},
        "code": {
            "coding": [{"display": clinical_scenario}],
            "text": clinical_scenario
        }
    }
    entries.append({"resource": condition_data})
    
    # 4. Lab Values (eGFR, INR, etc.)
    # eGFR
    egfr_match = re.search(r"egfr\s*(?:is|of|=)?\s*(\d+(?:\.\d+)?)", clinical_scenario, re.IGNORECASE)
    if egfr_match:
        val = float(egfr_match.group(1))
        obs_data = {
            "id": "observation-egfr",
            "resourceType": "Observation",
            "status": "final",
            "code": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": "62238-1",
                    "display": "Glomerular filtration rate/1.73 sq M.predicted"
                }],
                "text": "eGFR"
            },
            "subject": {"reference": "Patient/patient-1"},
            "effectiveDateTime": date.today().isoformat(),
            "valueQuantity": {
                "value": val,
                "unit": "mL/min/1.73m²"
            }
        }
        entries.append({"resource": obs_data})
        
    # INR
    inr_match = re.search(r"inr\s*(?:is|of|=)?\s*(\d+(?:\.\d+)?)", clinical_scenario, re.IGNORECASE)
    if inr_match:
        val = float(inr_match.group(1))
        obs_data = {
            "id": "observation-inr",
            "resourceType": "Observation",
            "status": "final",
            "code": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": "32960-9",
                    "display": "Prothrombin time INR"
                }],
                "text": "INR"
            },
            "subject": {"reference": "Patient/patient-1"},
            "effectiveDateTime": date.today().isoformat(),
            "valueQuantity": {
                "value": val
            }
        }
        entries.append({"resource": obs_data})
        
    # Platelets
    plt_match = re.search(r"(?:platelets|platelet|plt)\s*(?:is|of|=)?\s*(\d+)", clinical_scenario, re.IGNORECASE)
    if plt_match:
        val = float(plt_match.group(1))
        obs_data = {
            "id": "observation-platelets",
            "resourceType": "Observation",
            "status": "final",
            "code": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": "777-3",
                    "display": "Platelets [#/volume] in Blood"
                }],
                "text": "Platelets"
            },
            "subject": {"reference": "Patient/patient-1"},
            "effectiveDateTime": date.today().isoformat(),
            "valueQuantity": {
                "value": val,
                "unit": "K/uL"
            }
        }
        entries.append({"resource": obs_data})
        
    # HCG / Pregnancy
    if re.search(r"\b(pregnant|pregnancy|hcg|positive\s+hcg)\b", clinical_scenario, re.IGNORECASE):
        obs_data = {
            "id": "observation-hcg",
            "resourceType": "Observation",
            "status": "final",
            "code": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": "8302-2",
                    "display": "hCG [Presence] in Urine/Serum"
                }],
                "text": "hCG"
            },
            "subject": {"reference": "Patient/patient-1"},
            "effectiveDateTime": date.today().isoformat(),
            "valueString": "positive"
        }
        entries.append({"resource": obs_data})
        
    # 5. Allergies (especially contrast agents)
    from medical_ontology import CONTRAST_ALLERGY_MAP
    allergy_idx = 1
    for key, val in CONTRAST_ALLERGY_MAP.items():
        if re.search(rf"\b{key}\b", clinical_scenario, re.IGNORECASE):
            allergy_data = {
                "id": f"allergy-{allergy_idx}",
                "resourceType": "AllergyIntolerance",
                "clinicalStatus": {
                    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]
                },
                "patient": {"reference": "Patient/patient-1"},
                "code": {
                    "coding": [
                        {"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": val["rxnorm"], "display": val.get("generic", key)},
                        {"system": "http://snomed.info/sct", "code": val["snomed_class"], "display": val["class_display"]}
                    ],
                    "text": key.capitalize()
                }
            }
            entries.append({"resource": allergy_data})
            allergy_idx += 1
            
    # 6. Medications
    from medical_ontology import MEDICATION_MAP
    med_idx = 1
    for key, val in MEDICATION_MAP.items():
        if re.search(rf"\b{key}\b", clinical_scenario, re.IGNORECASE):
            med_data = {
                "id": f"medication-{med_idx}",
                "resourceType": "MedicationStatement",
                "status": "active",
                "subject": {"reference": "Patient/patient-1"},
                "medication": {
                    "concept": {
                        "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": val["rxnorm"], "display": val.get("generic", key)}],
                        "text": key.capitalize()
                    }
                }
            }
            entries.append({"resource": med_data})
            med_idx += 1
            
    bundle_data = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": entries
    }
    return Bundle(**bundle_data)


def convert_text_to_fhir_bundle(clinical_scenario: str) -> Bundle:
    """
    Takes a raw clinical scenario text, extracts entities using an LLM, 
    and returns a compliant fhir.resources Bundle.
    
    Now also extracts:
    - Observation resources (lab values: eGFR, INR, Platelets, HCG, etc.)
    - AllergyIntolerance resources (contrast agent allergies)
    - MedicationStatement resources (anticoagulants, antiplatelets)
    """
    from datetime import date
    
    try:
        llm = get_extraction_llm()
        structured_llm = llm.with_structured_output(ClinicalExtraction)
        
        extracted: ClinicalExtraction = structured_llm.invoke(
            "Extract ALL of the following from the clinical scenario below:\n"
            "1. Patient demographics (age, gender, date of birth)\n"
            "2. Clinical conditions/indications (primary symptom, diagnosis, or mechanism of injury. Proactively append generalized clinical categorization terms, injury mechanisms, or suspected clinical syndromes such as 'major blunt trauma', 'cauda equina syndrome', 'spine trauma', 'low back pain', or 'head trauma' if the scenario implies them, to aid downstream guideline lookup)\n"
            "3. Requested imaging modalities\n"
            "4. Any lab values mentioned (eGFR, INR, Platelets, Hemoglobin, Fibrinogen, HCG, Creatinine, etc.)\n"
            "5. Any allergies mentioned (especially contrast agents like Omnipaque, Isovue, gadolinium, iodine)\n"
            "6. Any current medications mentioned (especially anticoagulants like Eliquis, Xarelto, Warfarin, Plavix, Heparin)\n"
            "7. Patient weight (in kg or lbs, convert to kg numeric value)\n"
            "8. Any recent scans or prior procedures (especially CT, MRI, X-rays with timeframes like '2 days ago' or 'last week')\n"
            "9. Any implants, pacemakers, cochlear implants, or claustrophobia\n"
            f"\nClinical Scenario:\n{clinical_scenario}"
        )
    except Exception as e:
        print(f"[WARN] Structured LLM FHIR extraction failed: {e}. Falling back to regex parser...")
        return fallback_text_to_fhir_bundle(clinical_scenario)
    
    entries = []
    
    # 1. Patient
    patient_data = {"id": "patient-1", "resourceType": "Patient"}
    if extracted.patient.gender and extracted.patient.gender.lower() in ["male", "female", "other", "unknown"]:
        patient_data["gender"] = extracted.patient.gender.lower()
    if extracted.patient.birth_date:
        patient_data["birthDate"] = extracted.patient.birth_date
    elif extracted.patient.age:
        # Approximate birthDate from age
        approx_year = date.today().year - extracted.patient.age
        patient_data["birthDate"] = f"{approx_year}-01-01"
    
    entries.append({"resource": patient_data})
    
    # 2. Conditions
    for i, cond in enumerate(extracted.conditions):
        condition_data = {
            "id": f"condition-{i+1}",
            "resourceType": "Condition",
            "clinicalStatus": {
                "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
            },
            "subject": {"reference": "Patient/patient-1"},
            "code": {
                "coding": [{"display": cond.description}],
                "text": cond.description
            }
        }
        entries.append({"resource": condition_data})
        
    # 3. ServiceRequest
    if extracted.service_request and extracted.service_request.modality:
        sr_data = {
            "id": "servicerequest-1",
            "resourceType": "ServiceRequest",
            "status": "draft",
            "intent": "proposal",
            "subject": {"reference": "Patient/patient-1"},
            "code": {
                "concept": {
                    "coding": [{"display": extracted.service_request.modality}],
                    "text": extracted.service_request.modality
                }
            }
        }
        entries.append({"resource": sr_data})
    
    # 4. Observations (Lab Values)
    loinc_map = {
        "egfr": ("69405-9", "Glomerular filtration rate"),
        "gfr": ("69405-9", "Glomerular filtration rate"),
        "inr": ("6301-6", "INR"),
        "platelets": ("777-3", "Platelet count"),
        "platelet": ("777-3", "Platelet count"),
        "hemoglobin": ("718-7", "Hemoglobin"),
        "hgb": ("718-7", "Hemoglobin"),
        "fibrinogen": ("3255-7", "Fibrinogen"),
        "hcg": ("2106-3", "Beta-HCG"),
        "beta-hcg": ("2106-3", "Beta-HCG"),
        "creatinine": ("2160-0", "Creatinine"),
    }
    
    for i, lab in enumerate(extracted.lab_values):
        lab_key = lab.lab_name.lower().replace(" ", "").replace("-", "")
        loinc_code, loinc_display = None, lab.lab_name
        for key, (code, display) in loinc_map.items():
            if key in lab_key:
                loinc_code, loinc_display = code, display
                break
        
        obs_data = {
            "id": f"observation-{i+1}",
            "resourceType": "Observation",
            "status": "final",
            "code": {
                "coding": [{"display": loinc_display}],
                "text": lab.lab_name
            },
            "subject": {"reference": "Patient/patient-1"},
            "effectiveDateTime": date.today().isoformat(),
        }
        if loinc_code:
            obs_data["code"]["coding"][0]["system"] = "http://loinc.org"
            obs_data["code"]["coding"][0]["code"] = loinc_code
        
        if lab.value is not None:
            obs_data["valueQuantity"] = {"value": lab.value}
            if lab.unit:
                obs_data["valueQuantity"]["unit"] = lab.unit
        elif lab.value_string:
            obs_data["valueString"] = lab.value_string
        
        entries.append({"resource": obs_data})
    
    # 5. AllergyIntolerance
    for i, allergy in enumerate(extracted.allergies):
        allergy_data = {
            "id": f"allergy-{i+1}",
            "resourceType": "AllergyIntolerance",
            "clinicalStatus": {
                "coding": [{"system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", "code": "active"}]
            },
            "patient": {"reference": "Patient/patient-1"},
            "code": {
                "coding": [{"display": allergy.substance}],
                "text": allergy.substance
            }
        }
        if allergy.reaction:
            allergy_data["reaction"] = [{"manifestation": [{"concept": {"coding": [{"display": allergy.reaction}], "text": allergy.reaction}}]}]
        entries.append({"resource": allergy_data})
    
    # 6. MedicationStatement (using R5 CodeableReference format for fhir.resources)
    for i, med in enumerate(extracted.medications):
        med_data = {
            "id": f"medication-{i+1}",
            "resourceType": "MedicationStatement",
            "status": "active",
            "subject": {"reference": "Patient/patient-1"},
            "medication": {
                "concept": {
                    "coding": [{"display": med.name}],
                    "text": med.name
                }
            }
        }
        if med.dose:
            med_data["dosage"] = [{"text": med.dose}]
        entries.append({"resource": med_data})
        
    # 7. Weight Observation
    if extracted.patient_weight_kg:
        weight_obs = {
            "id": "observation-weight",
            "resourceType": "Observation",
            "status": "final",
            "code": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": "29463-7",
                    "display": "Body weight"
                }],
                "text": "Body Weight"
            },
            "subject": {"reference": "Patient/patient-1"},
            "effectiveDateTime": date.today().isoformat(),
            "valueQuantity": {
                "value": extracted.patient_weight_kg,
                "unit": "kg"
            }
        }
        entries.append({"resource": weight_obs})

    # 8. Implants Conditions
    for i, implant in enumerate(extracted.implants):
        implant_cond = {
            "id": f"condition-implant-{i+1}",
            "resourceType": "Condition",
            "clinicalStatus": {
                "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
            },
            "subject": {"reference": "Patient/patient-1"},
            "code": {
                "coding": [{"display": implant}],
                "text": implant
            }
        }
        entries.append({"resource": implant_cond})

    # 9. Recent Procedures
    for i, proc in enumerate(extracted.recent_procedures):
        import re
        from datetime import date, timedelta
        
        proc_date = date.today().isoformat()
        days_match = re.search(r"(\d+)\s*day", proc, re.IGNORECASE)
        if days_match:
            days_ago = int(days_match.group(1))
            proc_date = (date.today() - timedelta(days=days_ago)).isoformat()
            
        proc_resource = {
            "id": f"procedure-recent-{i+1}",
            "resourceType": "Procedure",
            "status": "completed",
            "code": {
                "coding": [{"display": proc}],
                "text": proc
            },
            "subject": {"reference": "Patient/patient-1"},
            "occurrenceDateTime": proc_date
        }
        entries.append({"resource": proc_resource})
        
    # Create Bundle
    bundle_data = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": entries
    }
    
    bundle_data = enrich_bundle_with_ontology(bundle_data)
    
    # This will validate against the official FHIR R4 schema
    return Bundle(**bundle_data)


def enrich_bundle_with_ontology(bundle_data: dict) -> dict:
    """
    Enriches FHIR bundle resource entries with standard ontology coding blocks
    (LOINC, RxNorm, SNOMED-CT) from medical_ontology.py.
    """
    from medical_ontology import LOINC_MAP, MEDICATION_MAP, CONTRAST_ALLERGY_MAP
    
    for entry in bundle_data.get("entry", []):
        resource = entry.get("resource", {})
        rtype = resource.get("resourceType")
        
        # 1. Observation (Lab Values)
        if rtype == "Observation":
            code_obj = resource.get("code", {})
            text = code_obj.get("text", "").lower().strip()
            
            # Map based on LOINC_MAP keys
            for key, val in LOINC_MAP.items():
                if key in text or text in key:
                    if "coding" not in code_obj:
                        code_obj["coding"] = []
                    
                    has_loinc = False
                    for c in code_obj["coding"]:
                        if c.get("system") == "http://loinc.org":
                            c["code"] = val["code"]
                            c["display"] = val["display"]
                            has_loinc = True
                            break
                    if not has_loinc:
                        code_obj["coding"].append({
                            "system": "http://loinc.org",
                            "code": val["code"],
                            "display": val["display"]
                        })
                    break
                    
        # 2. AllergyIntolerance
        elif rtype == "AllergyIntolerance":
            code_obj = resource.get("code", {})
            text = code_obj.get("text", "").lower().strip()
            
            for key, val in CONTRAST_ALLERGY_MAP.items():
                if key in text:
                    if "coding" not in code_obj:
                        code_obj["coding"] = []
                    
                    # Inject specific RxNorm coding for substance
                    has_rxnorm = False
                    for c in code_obj["coding"]:
                        if c.get("system") == "http://www.nlm.nih.gov/research/umls/rxnorm":
                            c["code"] = val["rxnorm"]
                            c["display"] = val.get("generic", key)
                            has_rxnorm = True
                            break
                    if not has_rxnorm:
                        code_obj["coding"].append({
                            "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                            "code": val["rxnorm"],
                            "display": val.get("generic", key)
                        })
                        
                    # Inject SNOMED-CT class coding
                    has_snomed = False
                    for c in code_obj["coding"]:
                        if c.get("system") == "http://snomed.info/sct":
                            c["code"] = val["snomed_class"]
                            c["display"] = val["class_display"]
                            has_snomed = True
                            break
                    if not has_snomed:
                        code_obj["coding"].append({
                            "system": "http://snomed.info/sct",
                            "code": val["snomed_class"],
                            "display": val["class_display"]
                        })
                    break
                    
        # 3. MedicationStatement
        elif rtype == "MedicationStatement":
            med_obj = resource.get("medication", {})
            concept_obj = med_obj.get("concept", {})
            if not concept_obj:
                concept_obj = {"coding": [], "text": med_obj.get("text", "")}
                med_obj["concept"] = concept_obj
                
            text = concept_obj.get("text", "").lower().strip()
            for key, val in MEDICATION_MAP.items():
                if key in text:
                    if "coding" not in concept_obj:
                        concept_obj["coding"] = []
                        
                    has_rxnorm = False
                    for c in concept_obj["coding"]:
                        if c.get("system") == "http://www.nlm.nih.gov/research/umls/rxnorm":
                            c["code"] = val["rxnorm"]
                            c["display"] = val.get("generic", key)
                            has_rxnorm = True
                            break
                    if not has_rxnorm:
                        concept_obj["coding"].append({
                            "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                            "code": val["rxnorm"],
                            "display": val.get("generic", key)
                        })
                    break
                    
    return bundle_data

def extract_scenario_from_bundle(bundle_dict: dict) -> str:
    """
    Helper function to convert a FHIR Bundle back into a text scenario for the RAG engine.
    """
    patient_info = "Unknown patient"
    conditions = []
    requests = []
    
    for entry in bundle_dict.get("entry", []):
        resource = entry.get("resource", {})
        rtype = resource.get("resourceType")
        
        if rtype == "Patient":
            gender = resource.get("gender", "")
            birth_date = resource.get("birthDate", "")
            age = None
            if birth_date:
                try:
                    if hasattr(birth_date, "year"):
                        birth_year = birth_date.year
                    else:
                        birth_year = int(str(birth_date).split('-')[0])
                    from datetime import datetime
                    age = datetime.now().year - birth_year
                except Exception:
                    pass
            if age is not None:
                patient_info = f"{age}-year-old {gender or 'patient'}"
            elif gender:
                patient_info = f"{gender} patient"
        elif rtype == "Condition":
            text = resource.get("code", {}).get("text", "")
            if text:
                conditions.append(text)
        elif rtype == "ServiceRequest":
            code_val = resource.get("code", {})
            text = code_val.get("concept", {}).get("text", "") if "concept" in code_val else code_val.get("text", "")
            if text:
                requests.append(text)
                
    scenario = f"{patient_info} with {', '.join(conditions)}."
    if requests:
        scenario += f" Requested: {', '.join(requests)}."
        
    return scenario
