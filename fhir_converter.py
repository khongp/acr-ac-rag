import os
import json
import re
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
    from llm_router import get_llm_fast
    return get_llm_fast(temperature=0.0)

def is_term_negated(text: str, term: str) -> bool:
    """
    Checks if a clinical term is negated in the text.
    Handles boundaries and stops negation propagation at clause boundaries (comma, but, however, except, presents with).
    """
    import re
    text_lower = text.lower()
    term_lower = term.lower()
    
    for match in re.finditer(rf'\b{re.escape(term_lower)}\b', text_lower):
        start_idx = match.start()
        sub = text_lower[max(0, start_idx - 40):start_idx]
        parts = re.split(r'[,;]|\b(?:but|however|except|presents\s+with|presents|has|presents\s+for)\b', sub)
        segment_to_check = parts[-1]
        
        if re.search(r'\b(?:no|denies|without|negative|neg|none)\b', segment_to_check):
            return True
    return False

COMMON_INDICATIONS = [
    "chest pain", "sob", "shortness of breath", "dyspnea", "headache", "dvt", 
    "pulmonary embolism", "pe", "stroke", "tia", "back pain", "low back pain", 
    "abdominal pain", "jaundice", "trauma", "blunt trauma", "hematuria", 
    "seizure", "syncope", "dizziness"
]

HIGH_RISK_MEDS = {
    "eliquis": "apixaban",
    "plavix": "clopidogrel",
    "coumadin": "warfarin",
    "xarelto": "rivaroxaban",
    "glucophage": "metformin"
}

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
    
    # 3. Clinical conditions (Indications)
    # Check for specific COMMON_INDICATIONS, filtering out negated ones
    conditions_extracted = []
    for term in COMMON_INDICATIONS:
        if re.search(rf"\b{re.escape(term)}\b", clinical_scenario, re.IGNORECASE):
            if not is_term_negated(clinical_scenario, term):
                conditions_extracted.append(term.capitalize())
                
    # Also check rule-outs (suspected conditions)
    for match in re.finditer(r'(?:r/o|eval for|suspected|evaluate for|rule out)\s+([a-zA-Z\s]+)', clinical_scenario, re.IGNORECASE):
        # Extract up to 3 words
        raw_cond = match.group(1).strip()
        cond_words = re.findall(r'\b\w+\b', raw_cond)[:3]
        if cond_words:
            suspected_cond = " ".join(cond_words).capitalize()
            if suspected_cond.lower() not in [c.lower() for c in conditions_extracted]:
                conditions_extracted.append(suspected_cond)

    # 3a. Implants & Claustrophobia check (Safety-critical conditions)
    # pacemaker/icd
    has_pacemaker = re.search(r'\b(?:pacemaker|icd|cardiac device)\b', clinical_scenario, re.IGNORECASE)
    if has_pacemaker and not is_term_negated(clinical_scenario, has_pacemaker.group(0)):
        conditions_extracted.append("Pacemaker")
    # metallic implant
    has_implant = re.search(r'\b(?:aneurysm clip|metallic clip|metallic implant|shrapnel|cochlear implant)\b', clinical_scenario, re.IGNORECASE)
    if has_implant and not is_term_negated(clinical_scenario, has_implant.group(0)):
        conditions_extracted.append("Metallic implant")
    # claustrophobia
    has_claustrophobia = re.search(r'\b(?:claustrophobia|claustrophobic)\b', clinical_scenario, re.IGNORECASE)
    if has_claustrophobia and not is_term_negated(clinical_scenario, has_claustrophobia.group(0)):
        conditions_extracted.append("Claustrophobia")

    # Add matched conditions to entries
    cond_idx = 1
    for cond in conditions_extracted:
        condition_data = {
            "id": f"condition-{cond_idx}",
            "resourceType": "Condition",
            "clinicalStatus": {
                "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
            },
            "subject": {"reference": "Patient/patient-1"},
            "code": {
                "coding": [{"display": cond}],
                "text": cond
            }
        }
        entries.append({"resource": condition_data})
        cond_idx += 1

    # Fallback to general condition display if no indications/implants matched
    if cond_idx == 1:
        # Sanitize clinical scenario from safety critical terms if negated
        clean_scenario = clinical_scenario
        if has_pacemaker and is_term_negated(clinical_scenario, has_pacemaker.group(0)):
            clean_scenario = re.sub(r'\b(?:pacemaker|icd|cardiac device)\b', 'device-negated', clean_scenario, flags=re.IGNORECASE)
        if has_implant and is_term_negated(clinical_scenario, has_implant.group(0)):
            clean_scenario = re.sub(r'\b(?:aneurysm clip|metallic clip|metallic implant|shrapnel|cochlear implant)\b', 'implant-negated', clean_scenario, flags=re.IGNORECASE)
        if has_claustrophobia and is_term_negated(clinical_scenario, has_claustrophobia.group(0)):
            clean_scenario = re.sub(r'\b(?:claustrophobia|claustrophobic)\b', 'claustrophobia-negated', clean_scenario, flags=re.IGNORECASE)
            
        condition_data = {
            "id": "condition-1",
            "resourceType": "Condition",
            "clinicalStatus": {
                "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
            },
            "subject": {"reference": "Patient/patient-1"},
            "code": {
                "coding": [{"display": clean_scenario}],
                "text": clean_scenario
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
    # De-duplicate medications by RxNorm code to prevent duplicate statements (e.g. brand + generic)
    meds_by_rxnorm = {}
    
    # Check default keys
    for key, val in MEDICATION_MAP.items():
        if re.search(rf"\b{key}\b", clinical_scenario, re.IGNORECASE):
            meds_by_rxnorm[val["rxnorm"]] = (key, val)
            
    # Check brand mappings
    for brand, generic in HIGH_RISK_MEDS.items():
        if re.search(rf"\b{brand}\b", clinical_scenario, re.IGNORECASE):
            if generic in MEDICATION_MAP:
                val = MEDICATION_MAP[generic]
                meds_by_rxnorm[val["rxnorm"]] = (generic, val)
            else:
                meds_by_rxnorm["unknown_" + generic] = (generic, {"rxnorm": "unknown", "generic": generic})
                
    for rxnorm_code, (key, val) in meds_by_rxnorm.items():
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


def _needs_llm_extraction(text: str) -> bool:
    """Returns True if the clinical text is complex enough to warrant LLM extraction."""
    if len(text.strip()) < 100:
        return False
    if is_generic_query(text):
        return False

    complexity_markers = 0
    if re.search(r'\d+\s*(?:mg|mcg|units?|ml)\b', text, re.IGNORECASE):
        complexity_markers += 1  # Dosages present
    if text.count(',') > 3 or text.count('.') > 3:
        complexity_markers += 1  # Multiple clauses
    if len(re.findall(r'\b(?:and|with|also|plus|additionally)\b', text, re.IGNORECASE)) > 2:
        complexity_markers += 1  # Multiple conditions joined
    if re.search(r'(?:post-?op|status.?post|s/p|prior)\b', text, re.IGNORECASE):
        complexity_markers += 1  # Surgical history

    return complexity_markers >= 2


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
    import re
    
    mode = os.getenv("FHIR_EXTRACTION_MODE", "auto").strip().lower()
    
    # Legacy env var support
    bypass = os.getenv("BYPASS_FHIR_LLM", "false").strip().lower() == "true"
    if bypass:
        mode = "regex"
    
    if mode == "regex":
        print("[FHIR BYPASS] Using fast local regex fallback for FHIR bundle extraction.")
        return fallback_text_to_fhir_bundle(clinical_scenario)
        
    elif mode == "auto":
        if not _needs_llm_extraction(clinical_scenario):
            print("[FHIR AUTO] Simple query — using fast local regex fallback for FHIR bundle extraction.")
            return fallback_text_to_fhir_bundle(clinical_scenario)
        print("[FHIR AUTO] Complex scenario — attempting LLM FHIR extraction.")
    else:
        print("[FHIR LLM] Always-LLM mode — attempting LLM FHIR extraction.")
    
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

def clean_demographics_prefix(text: str) -> str:
    """
    Helper to strip age, gender, and 'presenting with' connectors from clinical text
    to avoid repetitive text like '69-year-old female with 69 year old female presenting with RUQ pain'.
    """
    import re
    t = text.strip()
    
    age_pattern = r"\b\d+\s*(?:yo|year-old|years-old|year\s+old|years\s+old|yr\s+old|y/o|y\.o\.|-year-old|-yo)\b"
    gender_pattern = r"\b(?:female|male|man|woman|gentleman|lady|boy|girl|patient)(?:\s+patient)?\b"
    demographics_pattern = rf"(?:(?:{age_pattern}\s*{gender_pattern})|(?:{gender_pattern}\s*{age_pattern})|{age_pattern}|{gender_pattern})"
    connector_pattern = r"\b(?:presenting\s+with|presents\s+with|presentation\s+of|presenting|presents|status-post|status\s+post|with)\b"
    
    prefix_regex = re.compile(
        rf"^[^\w]*(?:{demographics_pattern})\s*(?:{connector_pattern})?\s*",
        re.IGNORECASE
    )
    
    prefix_regex_fallback = re.compile(
        rf"^[^\w]*(?:{demographics_pattern})[^\w]*",
        re.IGNORECASE
    )
    
    match = prefix_regex.match(t)
    if match:
        cleaned = t[match.end():].strip()
        cleaned = cleaned.lstrip(":,;- ").strip()
        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]
        return cleaned
        
    match = prefix_regex_fallback.match(t)
    if match:
        cleaned = t[match.end():].strip()
        cleaned = cleaned.lstrip(":,;- ").strip()
        if cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]
        return cleaned
        
    return text

def extract_demographics_from_text(text: str):
    """
    Extract age and gender from clinical text if present.
    Returns (age: int or None, gender: str or None).
    """
    import re
    age = None
    gender = None
    
    age_match = re.search(
        r'\b(\d+)\s*(?:yo|year-old|years-old|year\s+old|years\s+old|yr\s+old|y/o|y\.o\.|-year-old|-yo)\b',
        text, re.IGNORECASE
    )
    if age_match:
        age = int(age_match.group(1))
    
    gender_match = re.search(r'\b(female|male)\b', text, re.IGNORECASE)
    if gender_match:
        gender = gender_match.group(1).lower()
    
    return age, gender

def extract_scenario_from_bundle(bundle_dict: dict) -> str:
    """
    Helper function to convert a FHIR Bundle back into a text scenario for the RAG engine.
    
    Demographics priority: If the Condition text contains embedded age/gender
    (e.g. '54 yo female with RUQ pain'), those are treated as the source of truth
    and override the Patient resource demographics (which may be stale preset data).
    """
    # Defaults from Patient resource
    patient_age = None
    patient_gender = ""
    raw_conditions = []
    requests = []
    
    for entry in bundle_dict.get("entry", []):
        resource = entry.get("resource", {})
        rtype = resource.get("resourceType")
        
        if rtype == "Patient":
            patient_gender = resource.get("gender", "")
            birth_date = resource.get("birthDate", "")
            if birth_date:
                try:
                    if hasattr(birth_date, "year"):
                        birth_year = birth_date.year
                    else:
                        birth_year = int(str(birth_date).split('-')[0])
                    from datetime import datetime
                    patient_age = datetime.now().year - birth_year
                except Exception:
                    pass
        elif rtype == "Condition":
            text = resource.get("code", {}).get("text", "")
            if text:
                raw_conditions.append(text)
        elif rtype == "ServiceRequest":
            code_val = resource.get("code", {})
            text = code_val.get("concept", {}).get("text", "") if "concept" in code_val else code_val.get("text", "")
            if text:
                requests.append(text)
    
    # Check if any condition text has its own embedded demographics.
    # If so, those are the source of truth (user typed them), and the Patient
    # resource demographics may be stale preset sidebar data.
    embedded_age = None
    embedded_gender = None
    for cond_text in raw_conditions:
        ea, eg = extract_demographics_from_text(cond_text)
        if ea is not None:
            embedded_age = ea
            if eg:
                embedded_gender = eg
            break  # use the first condition with demographics
    
    # Build patient_info: prefer embedded demographics over Patient resource
    final_age = embedded_age if embedded_age is not None else patient_age
    final_gender = embedded_gender or patient_gender or "patient"
    
    if final_age is not None:
        patient_info = f"{final_age}-year-old {final_gender}"
    elif final_gender and final_gender != "patient":
        patient_info = f"{final_gender} patient"
    else:
        patient_info = "Unknown patient"
    
    # Clean demographics prefix from condition and request texts
    cleaned_conditions = []
    for c in raw_conditions:
        cleaned = clean_demographics_prefix(c)
        if cleaned:
            cleaned_conditions.append(cleaned)
    
    cleaned_requests = []
    for r in requests:
        cleaned = clean_demographics_prefix(r)
        if cleaned:
            cleaned_requests.append(cleaned)
                
    scenario = f"{patient_info} with {', '.join(cleaned_conditions)}."
    if cleaned_requests:
        scenario += f" Requested: {', '.join(cleaned_requests)}."
        
    return scenario


def is_generic_query(text: str) -> bool:
    """
    Determines if a query is a general knowledge search rather than a specific patient case.
    To avoid false positives, this check is hyper-conservative.
    """
    text_lower = text.strip().lower()
    
    # 1. Must start with explicit question or guidelines search prefix
    generic_prefixes = (
        "how to", "what is", "what are", "treatment for", "guidelines for",
        "recommendation for", "recommendations for", "protocol for", "protocols for",
        "workup for", "management of", "diagnose", "diagnosis of", "evaluation of",
        "appropriate scan for", "appropriate imaging for", "imaging for", "indication for"
    )
    starts_with_prefix = any(text_lower.startswith(prefix) for prefix in generic_prefixes)
    if not starts_with_prefix:
        return False
        
    # 2. Must not contain any patient demographics or case presentation markers
    # (avoid bypassing actual patient cases described with prefixes like "guidelines for...")
    patient_keywords = {
        # Demographics / age / gender
        "yo", "year-old", "year old", "years old", "y.o.", "y/o", "male", "female", 
        "man", "woman", "boy", "girl", "infant", "toddler", "pediatric", "child", "adult", 
        "pregnant", "pregnancy", "gestational",
        
        # Clinical case markers
        "patient", "pt", "presents", "presented", "complaining", "complains", 
        "hx of", "history of", "diagnosed with", "admitted", "symptoms of", "symptom of",
        "vitals", "bp", "heart rate",
        
        # Safety/implant/allergy markers
        "pacemaker", "allergy", "allergies", "allergic", "egfr", "creatinine", 
        "gfr", "inr", "platelet", "platelets", "hemoglobin", "hgb", "weight", "kg", "lbs", 
        "implant", "shunt", "metal", "stent", "cochlear", "claustrophobia", "claustrophobic",
        "contrast allergy", "allergy to",
        
        # Medications
        "eliquis", "apixaban", "warfarin", "coumadin", "plavix", "clopidogrel", "aspirin", 
        "metformin", "heparin", "lovenox", "enoxaparin"
    }
    
    import re
    # Check if any patient keyword is present as a whole word
    for word in patient_keywords:
        pattern = r"\b" + re.escape(word) + r"\b"
        if re.search(pattern, text_lower):
            return False
            
    return True

