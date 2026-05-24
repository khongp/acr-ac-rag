"""
Medical Ontology Mappings (RxNorm, LOINC, SNOMED-CT)
Contains mappings of brand/generic names of medications, contrast agents, and lab tests.
Used to programmatically enrich FHIR bundles and evaluate safety rules deterministically.
"""

LOINC_MAP = {
    "egfr": {"code": "62238-1", "display": "Glomerular filtration rate/1.73 sq M.predicted"},
    "inr": {"code": "32960-9", "display": "Prothrombin time INR"},
    "platelets": {"code": "777-3", "display": "Platelets [#/volume] in Blood"},
    "hgb": {"code": "718-7", "display": "Hemoglobin [Mass/volume] in Blood"},
    "fibrinogen": {"code": "3255-7", "display": "Fibrinogen [Mass/volume] in Coagulating blood"},
    "hcg": {"code": "8302-2", "display": "hCG [Presence] in Urine/Serum"},
}

MEDICATION_MAP = {
    # Anticoagulants
    "eliquis": {"generic": "apixaban", "rxnorm": "1364430", "class": "anticoagulant"},
    "apixaban": {"generic": "apixaban", "rxnorm": "1364430", "class": "anticoagulant"},
    "xarelto": {"generic": "rivaroxaban", "rxnorm": "1114185", "class": "anticoagulant"},
    "rivaroxaban": {"generic": "rivaroxaban", "rxnorm": "1114185", "class": "anticoagulant"},
    "coumadin": {"generic": "warfarin", "rxnorm": "11289", "class": "anticoagulant"},
    "warfarin": {"generic": "warfarin", "rxnorm": "11289", "class": "anticoagulant"},
    "lovenox": {"generic": "enoxaparin", "rxnorm": "78514", "class": "anticoagulant"},
    "enoxaparin": {"generic": "enoxaparin", "rxnorm": "78514", "class": "anticoagulant"},
    "heparin": {"generic": "heparin", "rxnorm": "5224", "class": "anticoagulant"},
    "pradaxa": {"generic": "dabigatran", "rxnorm": "1000100", "class": "anticoagulant"},
    "dabigatran": {"generic": "dabigatran", "rxnorm": "1000100", "class": "anticoagulant"},
    
    # Antiplatelets
    "aspirin": {"generic": "aspirin", "rxnorm": "1191", "class": "antiplatelet"},
    "plavix": {"generic": "clopidogrel", "rxnorm": "32968", "class": "antiplatelet"},
    "clopidogrel": {"generic": "clopidogrel", "rxnorm": "32968", "class": "antiplatelet"},
    "effient": {"generic": "prasugrel", "rxnorm": "613391", "class": "antiplatelet"},
    "prasugrel": {"generic": "prasugrel", "rxnorm": "613391", "class": "antiplatelet"},
    "brilinta": {"generic": "ticagrelor", "rxnorm": "1116632", "class": "antiplatelet"},
    "ticagrelor": {"generic": "ticagrelor", "rxnorm": "1116632", "class": "antiplatelet"},
}

CONTRAST_ALLERGY_MAP = {
    # Iodinated Contrast (Brand and Generics)
    "omnipaque": {"generic": "iohexol", "rxnorm": "37207", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    "iohexol": {"generic": "iohexol", "rxnorm": "37207", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    "visipaque": {"generic": "iodixanol", "rxnorm": "70265", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    "iodixanol": {"generic": "iodixanol", "rxnorm": "70265", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    "ultravist": {"generic": "iopromide", "rxnorm": "65241", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    "iopromide": {"generic": "iopromide", "rxnorm": "65241", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    "optiray": {"generic": "ioversol", "rxnorm": "67098", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    "ioversol": {"generic": "ioversol", "rxnorm": "67098", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    "iodine": {"generic": "iodine", "rxnorm": "5865", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    "iodinated": {"generic": "iodine", "rxnorm": "5865", "snomed_class": "387402008", "class_display": "Iodinated contrast agent"},
    
    # Gadolinium-Based Contrast Agents (GBCA)
    "gadavist": {"generic": "gadobutrol", "rxnorm": "1158580", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "gadobutrol": {"generic": "gadobutrol", "rxnorm": "1158580", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "multihance": {"generic": "gadobenate", "rxnorm": "284393", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "gadobenate": {"generic": "gadobenate", "rxnorm": "284393", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "dotarem": {"generic": "gadoterate", "rxnorm": "1370849", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "gadoterate": {"generic": "gadoterate", "rxnorm": "1370849", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "prohance": {"generic": "gadoteridol", "rxnorm": "125695", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "gadoteridol": {"generic": "gadoteridol", "rxnorm": "125695", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "magnevist": {"generic": "gadopentetate", "rxnorm": "64380", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "gadopentetate": {"generic": "gadopentetate", "rxnorm": "64380", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
    "gadolinium": {"generic": "gadolinium", "rxnorm": "4674", "snomed_class": "261000122102", "class_display": "Gadolinium contrast agent"},
}
