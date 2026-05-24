-- =============================================
-- Sky Ridge Medical Center — Seed Data
-- =============================================
-- Run with: python -c "from protocol_db import *; initialize_db(); exec(open('data/protocols/skyridge_seed.sql').read())"
-- Or via the seed_db.py script.

-- ─────────────────────────────────────────────
-- Institution
-- ─────────────────────────────────────────────
INSERT OR IGNORE INTO institution (id, name, ehr_system, timezone)
VALUES ('skyridge', 'Sky Ridge Medical Center', 'Epic', 'America/Denver');


-- ─────────────────────────────────────────────
-- Scanners
-- ─────────────────────────────────────────────
INSERT OR IGNORE INTO scanner (id, institution_id, modality, manufacturer, model, capabilities)
VALUES 
    ('sr_ct_force',    'skyridge', 'CT',  'Siemens', 'SOMATOM Force',   '["dual_energy","tin_filter","turbo_flash"]'),
    ('sr_ct_go_top',   'skyridge', 'CT',  'Siemens', 'SOMATOM go.Top',  '["standard"]'),
    ('sr_mri_vida',    'skyridge', 'MRI', 'Siemens', 'MAGNETOM Vida 3T','["dti","spectroscopy","cardiac"]'),
    ('sr_mri_aera',    'skyridge', 'MRI', 'Siemens', 'MAGNETOM Aera 1.5T','["standard","mra"]'),
    ('sr_us_ge',       'skyridge', 'US',  'GE',      'LOGIQ E10',       '["elastography","contrast_enhanced"]'),
    ('sr_fluoro_artis','skyridge', 'FLUORO','Siemens','Artis pheno',     '["dsa","cone_beam_ct","roadmap"]');


-- ─────────────────────────────────────────────
-- Diagnostic Imaging Protocols
-- ─────────────────────────────────────────────

-- CT Abdomen Pelvis — Appendicitis
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type, contrast_agent, contrast_volume_ml,
    contrast_rate_ml_s, phases, oral_prep, oral_prep_conditions,
    slice_thickness_mm, reconstruction, requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'sr_ct_abd_pelvis_appendix', 'skyridge',
    'CT Abdomen Pelvis — Appendicitis Protocol',
    'CT', 'abdomen_pelvis',
    'RLQ pain, suspected appendicitis, acute abdomen',
    'iv', 'Omnipaque 350', 100.0, 3.0,
    '["portal_venous"]',
    '900mL Volumen 1hr prior',
    '{"bmi_lt": 25}',
    1.25,
    '["soft_tissue","bone"]',
    1,
    'Single portal venous phase. Delayed only if concern for ureteral stone.',
    10, 'System Seed v1'
);

-- CT Head Without Contrast — Stroke / Hemorrhage
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type,
    slice_thickness_mm, reconstruction, requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'sr_ct_head_noncon', 'skyridge',
    'CT Head Without Contrast — Stroke / ICH Screen',
    'CT', 'head',
    'Acute neurological deficit, thunderclap headache, trauma, altered mental status',
    'none',
    5.0,
    '["soft_tissue","bone"]',
    0,
    'Axial helical from foramen magnum to vertex. Coronal and sagittal reformats.',
    5, 'System Seed v1'
);

-- CTA Head and Neck — Large Vessel Occlusion
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type, contrast_agent, contrast_volume_ml,
    contrast_rate_ml_s, phases,
    slice_thickness_mm, reconstruction, requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'sr_cta_head_neck_lvo', 'skyridge',
    'CTA Head and Neck — LVO / Stroke Protocol',
    'CT', 'head_neck',
    'Acute stroke, NIHSS ≥6, LVO screening',
    'iv', 'Isovue 370', 80.0, 4.0,
    '["arterial"]',
    0.625,
    '["soft_tissue","MIP","VR"]',
    1,
    'Bolus trigger at aortic arch. Cover from arch to vertex. Immediate notification to neuro if LVO detected.',
    8, 'System Seed v1'
);

-- CT Chest PE Study
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type, contrast_agent, contrast_volume_ml,
    contrast_rate_ml_s, phases,
    slice_thickness_mm, reconstruction, requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'sr_ct_chest_pe', 'skyridge',
    'CT Chest — Pulmonary Embolism Protocol',
    'CT', 'chest',
    'Chest pain, dyspnea, elevated D-dimer, suspected PE, DVT',
    'iv', 'Omnipaque 350', 75.0, 4.5,
    '["pulmonary_arterial"]',
    1.25,
    '["soft_tissue","lung","MIP"]',
    1,
    'Bolus tracking at main PA. Scan during peak PA opacification. Include upper abdomen for hepatic vein assessment if DVT concern.',
    8, 'System Seed v1'
);

-- MRI Brain With and Without Contrast
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, scanner_id, name, modality, body_region,
    clinical_indication, contrast_type, contrast_agent, contrast_volume_ml,
    requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'sr_mri_brain_wwoc', 'skyridge', 'sr_mri_vida',
    'MRI Brain Without and With Contrast — Standard',
    'MRI', 'head',
    'Headache, mass, metastatic workup, MS, infection, cranial nerve palsy',
    'iv', 'Gadavist', 10.0,
    1,
    '3T preferred. Weight-based gadolinium dosing: 0.1 mmol/kg.',
    35, 'System Seed v1'
);

-- MRI Brain Sequences (protocol_step)
INSERT OR IGNORE INTO protocol_step (protocol_id, step_order, sequence_name, timing_description, notes) VALUES
    ('sr_mri_brain_wwoc', 1, 'Sag T1 MPRAGE',            'Pre-contrast',                    '1mm isotropic'),
    ('sr_mri_brain_wwoc', 2, 'Ax T2 TSE',                 'Pre-contrast',                    '4mm slices'),
    ('sr_mri_brain_wwoc', 3, 'Ax FLAIR',                   'Pre-contrast',                    '4mm slices'),
    ('sr_mri_brain_wwoc', 4, 'Ax DWI (b=0, b=1000)',      'Pre-contrast',                    'ADC map auto-generated'),
    ('sr_mri_brain_wwoc', 5, 'Ax SWI',                     'Pre-contrast',                    'Susceptibility weighted'),
    ('sr_mri_brain_wwoc', 6, '--- IV Gadolinium ---',      'Inject Gadavist 0.1 mmol/kg',     'Power inject at 2 mL/s, 20mL saline flush'),
    ('sr_mri_brain_wwoc', 7, 'Ax T1 MPRAGE +C',           '5 min post-injection',            '1mm isotropic'),
    ('sr_mri_brain_wwoc', 8, 'Cor T1 SE +C',              '8 min post-injection',            '4mm slices'),
    ('sr_mri_brain_wwoc', 9, 'Sag T1 MPRAGE +C',          '10 min post-injection',           '1mm isotropic, for 3D reformat');

-- US Right Upper Quadrant
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type,
    requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'sr_us_ruq', 'skyridge',
    'US Right Upper Quadrant — Gallbladder / Biliary',
    'US', 'abdomen',
    'RUQ pain, nausea, elevated LFTs, suspected cholecystitis, cholelithiasis',
    'none',
    0,
    'Patient NPO ≥6 hours preferred. Include gallbladder (long, trans, with/without compression), CBD measurement, liver parenchyma, right kidney.',
    20, 'System Seed v1'
);


-- ─────────────────────────────────────────────
-- Contrast / Safety Rules
-- ─────────────────────────────────────────────

-- eGFR rules for all IV contrast protocols
INSERT OR IGNORE INTO contrast_rule (protocol_id, rule_type, condition_json, action_if_triggered, alert_message, severity)
VALUES
    ('sr_ct_abd_pelvis_appendix', 'egfr_check',
     '{"egfr_min": 30, "max_age_days": 90}',
     'flag', '⚠️ eGFR < 30 — IV iodinated contrast risk. Consider US or MRI alternative.', 'warning'),
    ('sr_cta_head_neck_lvo', 'egfr_check',
     '{"egfr_min": 30, "max_age_days": 90}',
     'flag', '⚠️ eGFR < 30 — However, for acute stroke, do NOT delay CTA for labs per AHA guidelines.', 'info'),
    ('sr_ct_chest_pe', 'egfr_check',
     '{"egfr_min": 30, "max_age_days": 90}',
     'flag', '⚠️ eGFR < 30 — IV contrast risk. However, if high clinical suspicion for PE, benefit likely outweighs risk.', 'warning'),
    ('sr_mri_brain_wwoc', 'egfr_check',
     '{"egfr_min": 30, "max_age_days": 90}',
     'flag', '⚠️ eGFR < 30 — Risk of Nephrogenic Systemic Fibrosis with gadolinium. Consider non-contrast MRI or Group II agent.', 'warning');

-- Allergy rules
INSERT OR IGNORE INTO contrast_rule (protocol_id, rule_type, condition_json, action_if_triggered, premedication_text, alert_message, severity)
VALUES
    ('sr_ct_abd_pelvis_appendix', 'allergy_check',
     '{"agents": ["Omnipaque","Isovue","iodinated contrast","iodine","contrast dye"]}',
     'require_premedication',
     'Prednisone 50mg PO at 13h, 7h, and 1h prior to exam. Diphenhydramine 50mg PO/IV 1h prior.',
     '⚠️ Iodinated contrast allergy documented — Pre-medication protocol required.', 'warning'),
    ('sr_cta_head_neck_lvo', 'allergy_check',
     '{"agents": ["Omnipaque","Isovue","iodinated contrast","iodine","contrast dye"]}',
     'flag',
     'For acute stroke: give Diphenhydramine 50mg IV and Hydrocortisone 200mg IV immediately. Do NOT delay CTA.',
     '⚠️ Contrast allergy — Emergent premedication per ACR guidelines. Do NOT delay for full prep.', 'warning'),
    ('sr_mri_brain_wwoc', 'allergy_check',
     '{"agents": ["Gadavist","gadolinium","Dotarem","Prohance","MultiHance"]}',
     'require_premedication',
     'Prednisone 50mg PO at 13h, 7h, and 1h prior. Diphenhydramine 50mg PO/IV 1h prior.',
     '⚠️ Gadolinium allergy documented — Pre-medication required.', 'warning');

-- Pregnancy rules for CT protocols
INSERT OR IGNORE INTO contrast_rule (protocol_id, rule_type, condition_json, action_if_triggered, substitute_protocol_id, alert_message, severity)
VALUES
    ('sr_ct_abd_pelvis_appendix', 'pregnancy_check',
     '{"sex": "female", "age_min": 12, "age_max": 55}',
     'substitute_protocol',
     'sr_us_ruq',
     '🚫 Female of childbearing age — Verify pregnancy status (β-HCG). Consider US or MRI as first-line.', 'hard_stop'),
    ('sr_ct_chest_pe', 'pregnancy_check',
     '{"sex": "female", "age_min": 12, "age_max": 55}',
     'flag',
     NULL,
     '⚠️ Female of childbearing age — Verify pregnancy status (β-HCG). If pregnant, consider V/Q scan over CTPA.', 'hard_stop');


-- ─────────────────────────────────────────────
-- IR Protocols
-- ─────────────────────────────────────────────

-- Percutaneous Liver Biopsy
INSERT OR IGNORE INTO ir_protocol (
    id, institution_id, name, procedure_category, body_region,
    sir_bleeding_risk, imaging_guidance, sedation_type,
    estimated_time_min,
    pre_procedure_instructions, post_procedure_instructions,
    special_equipment, updated_by
) VALUES (
    'sr_ir_liver_biopsy', 'skyridge',
    'Percutaneous Liver Biopsy',
    'biopsy', 'liver', 'significant', 'US', 'moderate',
    45,
    'NPO after midnight. IV access (18g or larger). Consent signed. Type & Screen if not done within 72h. Hold anticoagulants per protocol.',
    'Bed rest 4 hours. Vitals Q15 x4, then Q30 x4, then Q1h x2. CBC at 4 hours post-procedure. CT abdomen if acute pain or hemodynamic instability.',
    '["18g_coaxial_introducer","18g_biopsy_gun","specimen_jars_formalin"]',
    'System Seed v1'
);

-- Percutaneous Nephrostomy (PCN)
INSERT OR IGNORE INTO ir_protocol (
    id, institution_id, name, procedure_category, body_region,
    sir_bleeding_risk, imaging_guidance, sedation_type,
    estimated_time_min,
    pre_procedure_instructions, post_procedure_instructions,
    special_equipment, updated_by
) VALUES (
    'sr_ir_pcn', 'skyridge',
    'Percutaneous Nephrostomy Tube Placement',
    'drainage', 'kidney', 'significant', 'Combo', 'moderate',
    60,
    'NPO after midnight. IV access. Consent signed. Antibiotics: Cefazolin 2g IV 1h prior (or Vancomycin if PCN allergy). Hold anticoagulants per protocol.',
    'Bed rest 2 hours. Vitals Q15 x4, then Q30 x4. Monitor urine output Q1h. Nephrostogram in 48-72h if clinically indicated. Flush tube Q8h with 10mL sterile saline.',
    '["22g_chiba_needle","0.018_wire","0.035_amplatz_wire","fascial_dilators","8fr_pigtail_catheter","drainage_bag"]',
    'System Seed v1'
);

-- Tunneled Dialysis Catheter
INSERT OR IGNORE INTO ir_protocol (
    id, institution_id, name, procedure_category, body_region,
    sir_bleeding_risk, imaging_guidance, sedation_type,
    estimated_time_min,
    pre_procedure_instructions, post_procedure_instructions,
    special_equipment, updated_by
) VALUES (
    'sr_ir_tunneled_hd_cath', 'skyridge',
    'Tunneled Hemodialysis Catheter Placement',
    'venous_access', 'chest', 'low', 'Combo', 'moderate',
    45,
    'NPO after midnight. IV access. Consent signed. Confirm catheter side with nephrology. CXR to confirm tip position.',
    'CXR post-procedure for line position. Catheter ready for use immediately. Heparin lock per dialysis protocol. Dressing change in 48h.',
    '["14.5fr_tunneled_hd_catheter","micropuncture_set","peel_away_sheath","tunnel_tool"]',
    'System Seed v1'
);


-- ─────────────────────────────────────────────
-- IR Lab Thresholds (SIR 2019 Consensus)
-- ─────────────────────────────────────────────

-- Liver Biopsy (Significant Bleeding Risk)
INSERT OR IGNORE INTO ir_lab_threshold (ir_protocol_id, lab_name, loinc_code, threshold_operator, threshold_value, max_result_age_hours, action_if_not_met, correction_guidance, sir_reference)
VALUES 
    ('sr_ir_liver_biopsy', 'INR',       '6301-6', '<=', 1.5,    72, 'hard_stop',          'Administer Vitamin K 10mg IV or FFP 2 units. Recheck INR in 6 hours.', 'SIR 2019 Consensus Guidelines, Table 2'),
    ('sr_ir_liver_biopsy', 'Platelets', '777-3',  '>=', 50000,  72, 'hard_stop',          'Transfuse 1 unit platelets (single donor). Recheck 1h post-transfusion. Target >50K.', 'SIR 2019 Consensus Guidelines, Table 2'),
    ('sr_ir_liver_biopsy', 'Hgb',       '718-7',  '>=', 7.0,    72, 'flag',               'Consider PRBC transfusion if symptomatic or high procedural risk.', 'SIR 2019 Consensus Guidelines'),
    ('sr_ir_liver_biopsy', 'Fibrinogen','3255-7',  '>=', 200.0,  72, 'correct_and_recheck','Cryoprecipitate 10 units. Recheck fibrinogen 1h post.', 'SIR 2019 Consensus Guidelines, Table 2');

-- PCN (Significant Bleeding Risk)
INSERT OR IGNORE INTO ir_lab_threshold (ir_protocol_id, lab_name, loinc_code, threshold_operator, threshold_value, max_result_age_hours, action_if_not_met, correction_guidance, sir_reference)
VALUES 
    ('sr_ir_pcn', 'INR',       '6301-6', '<=', 1.5,    72, 'hard_stop',          'Administer Vitamin K or FFP. Recheck INR.', 'SIR 2019 Consensus Guidelines, Table 2'),
    ('sr_ir_pcn', 'Platelets', '777-3',  '>=', 50000,  72, 'hard_stop',          'Transfuse platelets. Target >50K.', 'SIR 2019 Consensus Guidelines, Table 2');

-- Tunneled HD Catheter (Low Bleeding Risk)
INSERT OR IGNORE INTO ir_lab_threshold (ir_protocol_id, lab_name, loinc_code, threshold_operator, threshold_value, max_result_age_hours, action_if_not_met, correction_guidance, sir_reference)
VALUES 
    ('sr_ir_tunneled_hd_cath', 'INR',       '6301-6', '<=', 2.0,    72, 'flag',    'Consider FFP if INR > 3.0. Low bleeding risk — may proceed with mild coagulopathy.', 'SIR 2019 Consensus Guidelines, Table 2'),
    ('sr_ir_tunneled_hd_cath', 'Platelets', '777-3',  '>=', 20000,  72, 'flag',    'Consider platelet transfusion. Low bleeding risk — may proceed.', 'SIR 2019 Consensus Guidelines, Table 2');


-- ─────────────────────────────────────────────
-- IR Med Holds (SIR 2019 Consensus)
-- ─────────────────────────────────────────────

-- Liver Biopsy — Significant risk → full holds
INSERT OR IGNORE INTO ir_med_hold (ir_protocol_id, medication_name, medication_class, rxnorm_code, hold_hours_before, resume_hours_after, bridging_required, bridging_protocol, renal_adjustment, sir_reference)
VALUES
    ('sr_ir_liver_biopsy', 'Apixaban (Eliquis)',        'doac',                   '1364430', 48, 24, 0, NULL, '{"egfr_lt_30": {"hold_hours_before": 72}}', 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_liver_biopsy', 'Rivaroxaban (Xarelto)',     'doac',                   '1114195', 48, 24, 0, NULL, '{"egfr_lt_30": {"hold_hours_before": 72}}', 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_liver_biopsy', 'Dabigatran (Pradaxa)',      'doac',                   '1037045', 72, 24, 0, NULL, '{"egfr_lt_30": {"hold_hours_before": 96}, "egfr_30_50": {"hold_hours_before": 72}}', 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_liver_biopsy', 'Warfarin (Coumadin)',       'vitamin_k_antagonist',   '11289',   120, 24, 1, 'Enoxaparin 1mg/kg BID; last dose 24h prior to procedure.', NULL, 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_liver_biopsy', 'Clopidogrel (Plavix)',      'antiplatelet',           '32968',   120, 24, 0, NULL, NULL, 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_liver_biopsy', 'Ticagrelor (Brilinta)',     'antiplatelet',           '1116632', 120, 24, 0, NULL, NULL, 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_liver_biopsy', 'Prasugrel (Effient)',       'antiplatelet',           '613391',  168, 24, 0, NULL, NULL, 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_liver_biopsy', 'Enoxaparin (Lovenox)',      'lmwh',                   '67108',   24,  24, 0, NULL, NULL, 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_liver_biopsy', 'Heparin IV',                'heparin',                '5224',    4,   24, 0, NULL, NULL, 'SIR 2019 Consensus, Table 4');

-- PCN — inherit same holds as liver biopsy (significant risk)
INSERT OR IGNORE INTO ir_med_hold (ir_protocol_id, medication_name, medication_class, rxnorm_code, hold_hours_before, resume_hours_after, bridging_required, bridging_protocol, renal_adjustment, sir_reference)
VALUES
    ('sr_ir_pcn', 'Apixaban (Eliquis)',        'doac',                   '1364430', 48, 24, 0, NULL, '{"egfr_lt_30": {"hold_hours_before": 72}}', 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_pcn', 'Rivaroxaban (Xarelto)',     'doac',                   '1114195', 48, 24, 0, NULL, '{"egfr_lt_30": {"hold_hours_before": 72}}', 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_pcn', 'Warfarin (Coumadin)',       'vitamin_k_antagonist',   '11289',   120, 24, 1, 'Enoxaparin 1mg/kg BID; last dose 24h prior.', NULL, 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_pcn', 'Clopidogrel (Plavix)',      'antiplatelet',           '32968',   120, 24, 0, NULL, NULL, 'SIR 2019 Consensus, Table 4'),
    ('sr_ir_pcn', 'Heparin IV',                'heparin',                '5224',    4,   24, 0, NULL, NULL, 'SIR 2019 Consensus, Table 4');

-- Tunneled HD Catheter — Low risk → minimal holds
INSERT OR IGNORE INTO ir_med_hold (ir_protocol_id, medication_name, medication_class, rxnorm_code, hold_hours_before, resume_hours_after, sir_reference)
VALUES
    ('sr_ir_tunneled_hd_cath', 'Heparin IV', 'heparin', '5224', 4, 0, 'SIR 2019 Consensus, Table 4');


-- ─────────────────────────────────────────────
-- ACR ↔ Protocol Bridge Mappings
-- ─────────────────────────────────────────────

INSERT OR IGNORE INTO acr_protocol_map (institution_id, acr_scenario_text, acr_procedure_text, acr_appropriateness, imaging_protocol_id, match_confidence, mapping_method, mapped_by)
VALUES
    -- RLQ Pain → CT Appendix
    ('skyridge', 'Right lower quadrant pain', 'CT abdomen and pelvis with IV contrast',
     'Usually appropriate', 'sr_ct_abd_pelvis_appendix', 0.95, 'manual_review', 'Dr. Khong'),

    -- Headache → CT Head Non-con
    ('skyridge', 'Headache', 'CT head without IV contrast',
     'Usually appropriate', 'sr_ct_head_noncon', 0.90, 'manual_review', 'Dr. Khong'),
    
    -- Acute stroke → CTA Head/Neck
    ('skyridge', 'New focal neurological deficit', 'CTA head and neck with IV contrast',
     'Usually appropriate', 'sr_cta_head_neck_lvo', 0.95, 'manual_review', 'Dr. Khong'),
    
    -- Suspected PE → CT PE
    ('skyridge', 'Suspected pulmonary embolism', 'CTA chest with IV contrast',
     'Usually appropriate', 'sr_ct_chest_pe', 0.95, 'manual_review', 'Dr. Khong'),
    ('skyridge', 'Acute dyspnea', 'CTA chest with IV contrast',
     'Usually appropriate', 'sr_ct_chest_pe', 0.80, 'manual_review', 'Dr. Khong'),
    
    -- Brain mass/infection → MRI Brain W/WO
    ('skyridge', 'Headache', 'MRI head without and with IV contrast',
     'Usually appropriate', 'sr_mri_brain_wwoc', 0.90, 'manual_review', 'Dr. Khong'),
    ('skyridge', 'New intracranial lesion', 'MRI head without and with IV contrast',
     'Usually appropriate', 'sr_mri_brain_wwoc', 0.95, 'manual_review', 'Dr. Khong'),
    
    -- RUQ Pain → US
    ('skyridge', 'Right upper quadrant pain', 'US abdomen',
     'Usually appropriate', 'sr_us_ruq', 0.90, 'manual_review', 'Dr. Khong');


-- =============================================
-- Denver General Hospital — Seed Data
-- =============================================

-- Institution
INSERT OR IGNORE INTO institution (id, name, ehr_system, timezone)
VALUES ('denver_general', 'Denver General Hospital', 'Cerner', 'America/Denver');


-- Scanners
INSERT OR IGNORE INTO scanner (id, institution_id, modality, manufacturer, model, capabilities)
VALUES 
    ('dg_ct_revolution', 'denver_general', 'CT',  'GE',      'Revolution Apex', '["dual_energy","spectral_imaging"]'),
    ('dg_mri_ingenia',    'denver_general', 'MRI', 'Philips', 'Ingenia Elition 3T','["mra","cardiac"]'),
    ('dg_us_epiq',        'denver_general', 'US',  'Philips', 'Epiq Elite',      '["elastography"]'),
    ('dg_fluoro_siemens', 'denver_general', 'FLUORO','Siemens','Artis zee',      '["dsa"]');


-- Diagnostic Imaging Protocols

-- CT Abdomen Pelvis — Appendicitis
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type, contrast_agent, contrast_volume_ml,
    contrast_rate_ml_s, phases, oral_prep, oral_prep_conditions,
    slice_thickness_mm, reconstruction, requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'dg_ct_abd_pelvis_appendix', 'denver_general',
    'DG CT Abdomen Pelvis — Appendix Protocol',
    'CT', 'abdomen_pelvis',
    'Suspected acute appendicitis or diverticulitis',
    'iv', 'Isovue 370', 90.0, 3.5,
    '["portal_venous"]',
    'Water 500mL 30 min prior',
    '{"bmi_lt": 25}',
    1.5,
    '["soft_tissue","bone_reformat"]',
    1,
    'Scan portal venous phase. Use spectral DECT if scanner available.',
    12, 'System Seed v1'
);

-- CT Head Without Contrast
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type,
    slice_thickness_mm, reconstruction, requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'dg_ct_head_noncon', 'denver_general',
    'DG CT Head Without Contrast — Acute Neuro Screen',
    'CT', 'head',
    'Altered mental status, stroke code, trauma, headache',
    'none',
    4.0,
    '["brain_recons","bone_recons"]',
    0,
    'Scan from base of skull to vertex. Reformat 4mm sagittal and coronal.',
    6, 'System Seed v1'
);

-- CTA Head and Neck
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type, contrast_agent, contrast_volume_ml,
    contrast_rate_ml_s, phases,
    slice_thickness_mm, reconstruction, requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'dg_cta_head_neck_lvo', 'denver_general',
    'DG CTA Head and Neck — Stroke Protocol',
    'CT', 'head_neck',
    'Acute neurological deficit, suspected stroke',
    'iv', 'Isovue 370', 70.0, 4.5,
    '["arterial"]',
    0.625,
    '["soft_tissue","MIP_axials","MIP_sagittals"]',
    1,
    'Bolus track aortic arch. Rapid infusion.',
    8, 'System Seed v1'
);

-- CT Chest PE Study
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type, contrast_agent, contrast_volume_ml,
    contrast_rate_ml_s, phases,
    slice_thickness_mm, reconstruction, requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'dg_ct_chest_pe', 'denver_general',
    'DG CT Chest — Pulmonary Embolism Protocol',
    'CT', 'chest',
    'Elevated D-dimer, acute chest pain or dyspnea',
    'iv', 'Isovue 370', 80.0, 4.0,
    '["pulmonary_arterial"]',
    1.25,
    '["soft_tissue","lung"]',
    1,
    'Bolus tracking on main pulmonary artery.',
    8, 'System Seed v1'
);

-- MRI Brain With and Without Contrast
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, scanner_id, name, modality, body_region,
    clinical_indication, contrast_type, contrast_agent, contrast_volume_ml,
    requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'dg_mri_brain_wwoc', 'denver_general', 'dg_mri_ingenia',
    'DG MRI Brain Without and With Contrast — Standard',
    'MRI', 'head',
    'Demyelinating disease, headache, mass, infection',
    'iv', 'Dotarem', 15.0,
    1,
    'Philips 3T preferred. Dosing per protocol.',
    40, 'System Seed v1'
);

-- MRI Brain Steps
INSERT OR IGNORE INTO protocol_step (protocol_id, step_order, sequence_name, timing_description, notes) VALUES
    ('dg_mri_brain_wwoc', 1, 'Sag T1 FFE',               'Pre-contrast',                    'Standard sagittal survey'),
    ('dg_mri_brain_wwoc', 2, 'Ax T2 TSE',                'Pre-contrast',                    'Philips standard T2'),
    ('dg_mri_brain_wwoc', 3, 'Ax FLAIR',                 'Pre-contrast',                    'Fluid attenuated inversion recovery'),
    ('dg_mri_brain_wwoc', 4, 'Ax DWI',                   'Pre-contrast',                    'b-value 1000'),
    ('dg_mri_brain_wwoc', 5, '--- IV Dotarem ---',       'Inject Dotarem 0.2 mL/kg',        'Hand injection or power injection'),
    ('dg_mri_brain_wwoc', 6, 'Ax T1 FFE +C',              'Post-contrast',                   'Post-contrast axial'),
    ('dg_mri_brain_wwoc', 7, 'Cor T1 FFE +C',             'Post-contrast',                   'Post-contrast coronal'),
    ('dg_mri_brain_wwoc', 8, 'Sag T1 3D +C',              'Post-contrast',                   'Isotropic 3D acquisition');

-- US Right Upper Quadrant
INSERT OR IGNORE INTO imaging_protocol (
    id, institution_id, name, modality, body_region,
    clinical_indication, contrast_type,
    requires_iv_access,
    special_instructions, estimated_time_min, updated_by
) VALUES (
    'dg_us_ruq', 'denver_general',
    'DG US Right Upper Quadrant — Biliary',
    'US', 'abdomen',
    'RUQ pain, suspected cholecystitis',
    'none',
    0,
    'Patient fasting NPO 6h.',
    25, 'System Seed v1'
);

-- Contrast / Safety Rules for Denver General
INSERT OR IGNORE INTO contrast_rule (protocol_id, rule_type, condition_json, action_if_triggered, alert_message, severity)
VALUES
    ('dg_ct_abd_pelvis_appendix', 'egfr_check',
     '{"egfr_min": 30, "max_age_days": 90}',
     'flag', '⚠️ Denver General Warning: eGFR < 30 — High risk for IV Isovue. Verify hydration or consider alternative.', 'warning'),
    ('dg_cta_head_neck_lvo', 'egfr_check',
     '{"egfr_min": 30, "max_age_days": 90}',
     'flag', 'ℹ️ eGFR < 30 — Hyperacute stroke: proceed with CTA immediately without waiting for labs.', 'info'),
    ('dg_ct_chest_pe', 'egfr_check',
     '{"egfr_min": 30, "max_age_days": 90}',
     'flag', '⚠️ eGFR < 30 — CTPA contrast risk. Perform risk/benefit assessment.', 'warning'),
    ('dg_mri_brain_wwoc', 'egfr_check',
     '{"egfr_min": 30, "max_age_days": 90}',
     'flag', '⚠️ eGFR < 30 — Nephrogenic Systemic Fibrosis risk with Dotarem. Consult radiologist.', 'warning');

INSERT OR IGNORE INTO contrast_rule (protocol_id, rule_type, condition_json, action_if_triggered, premedication_text, alert_message, severity)
VALUES
    ('dg_ct_abd_pelvis_appendix', 'allergy_check',
     '{"agents": ["Isovue","Omnipaque","iodinated contrast","iodine"]}',
     'require_premedication',
     'Methylprednisolone 32mg PO 12h and 2h prior to scan. Diphenhydramine 50mg IV 1h prior.',
     '⚠️ Isovue/Iodinated Contrast allergy — Denver General standard premedication protocol required.', 'warning'),
    ('dg_cta_head_neck_lvo', 'allergy_check',
     '{"agents": ["Isovue","Omnipaque","iodinated contrast","iodine"]}',
     'flag',
     'Emergency stroke premed: Hydrocortisone 200mg IV stat + Diphenhydramine 50mg IV. Do NOT delay CTA.',
     '⚠️ Emergent stroke premedication required for contrast allergy.', 'warning'),
    ('dg_mri_brain_wwoc', 'allergy_check',
     '{"agents": ["Dotarem","gadolinium","Gadavist"]}',
     'require_premedication',
     'Methylprednisolone 32mg PO 12h and 2h prior. Diphenhydramine 50mg PO 1h prior.',
     '⚠️ Gadolinium/Dotarem allergy — Premedication protocol required.', 'warning');

INSERT OR IGNORE INTO contrast_rule (protocol_id, rule_type, condition_json, action_if_triggered, substitute_protocol_id, alert_message, severity)
VALUES
    ('dg_ct_abd_pelvis_appendix', 'pregnancy_check',
     '{"sex": "female", "age_min": 12, "age_max": 55}',
     'substitute_protocol',
     'dg_us_ruq',
     '🚫 Pregnancy Risk — Verify HCG. Denver General protocol mandates US or MRI alternative.', 'hard_stop'),
    ('dg_ct_chest_pe', 'pregnancy_check',
     '{"sex": "female", "age_min": 12, "age_max": 55}',
     'flag',
     NULL,
     '⚠️ Pregnancy Risk — CTPA vs V/Q risk assessment required.', 'hard_stop');

-- IR Protocols
INSERT OR IGNORE INTO ir_protocol (
    id, institution_id, name, procedure_category, body_region,
    sir_bleeding_risk, imaging_guidance, sedation_type,
    estimated_time_min,
    pre_procedure_instructions, post_procedure_instructions,
    special_equipment, updated_by
) VALUES (
    'dg_ir_liver_biopsy', 'denver_general',
    'DG Percutaneous Liver Biopsy',
    'biopsy', 'liver', 'significant', 'US', 'moderate',
    40,
    'NPO after midnight. CBC/PT/INR labs on file. Confirm consent.',
    'Bed rest 4h. Vital signs monitoring. Post-op hematocrit check.',
    '["18g_biopsy_needle","ultrasound_sterile_drape"]',
    'System Seed v1'
);

INSERT OR IGNORE INTO ir_protocol (
    id, institution_id, name, procedure_category, body_region,
    sir_bleeding_risk, imaging_guidance, sedation_type,
    estimated_time_min,
    pre_procedure_instructions, post_procedure_instructions,
    special_equipment, updated_by
) VALUES (
    'dg_ir_tunneled_hd_cath', 'denver_general',
    'DG Tunneled Hemodialysis Catheter Placement',
    'venous_access', 'chest', 'low', 'Fluoro', 'moderate',
    40,
    'Consent signed. NPO 6h. Confirm dialysis side.',
    'Post-procedure chest X-ray to confirm tip location.',
    '["14.5fr_hemodialysis_catheter","dilators_set"]',
    'System Seed v1'
);

-- IR Lab Thresholds
INSERT OR IGNORE INTO ir_lab_threshold (ir_protocol_id, lab_name, loinc_code, threshold_operator, threshold_value, max_result_age_hours, action_if_not_met, correction_guidance, sir_reference)
VALUES 
    ('dg_ir_liver_biopsy', 'INR',       '6301-6', '<=', 1.5,    72, 'hard_stop',          'Correct with FFP or Vitamin K. Recheck.', 'SIR Guidelines'),
    ('dg_ir_liver_biopsy', 'Platelets', '777-3',  '>=', 50000,  72, 'hard_stop',          'Transfuse platelets if < 50k.', 'SIR Guidelines');

-- IR Med Holds
INSERT OR IGNORE INTO ir_med_hold (ir_protocol_id, medication_name, medication_class, rxnorm_code, hold_hours_before, resume_hours_after, bridging_required, bridging_protocol, renal_adjustment, sir_reference)
VALUES
    ('dg_ir_liver_biopsy', 'Apixaban (Eliquis)',        'doac',                   '1364430', 48, 24, 0, NULL, '{"egfr_lt_30": {"hold_hours_before": 72}}', 'SIR Consensus'),
    ('dg_ir_liver_biopsy', 'Rivaroxaban (Xarelto)',     'doac',                   '1114195', 48, 24, 0, NULL, '{"egfr_lt_30": {"hold_hours_before": 72}}', 'SIR Consensus'),
    ('dg_ir_liver_biopsy', 'Warfarin (Coumadin)',       'vitamin_k_antagonist',   '11289',   120, 24, 1, 'Lovenox bridging.', NULL, 'SIR Consensus'),
    ('dg_ir_liver_biopsy', 'Clopidogrel (Plavix)',      'antiplatelet',           '32968',   120, 24, 0, NULL, NULL, 'SIR Consensus');

-- ACR ↔ Protocol Bridge Mappings for Denver General
INSERT OR IGNORE INTO acr_protocol_map (institution_id, acr_scenario_text, acr_procedure_text, acr_appropriateness, imaging_protocol_id, match_confidence, mapping_method, mapped_by)
VALUES
    ('denver_general', 'Right lower quadrant pain', 'CT abdomen and pelvis with IV contrast',
     'Usually appropriate', 'dg_ct_abd_pelvis_appendix', 0.95, 'manual_review', 'Dr. Adams'),
    ('denver_general', 'Headache', 'CT head without IV contrast',
     'Usually appropriate', 'dg_ct_head_noncon', 0.90, 'manual_review', 'Dr. Adams'),
    ('denver_general', 'New focal neurological deficit', 'CTA head and neck with IV contrast',
     'Usually appropriate', 'dg_cta_head_neck_lvo', 0.95, 'manual_review', 'Dr. Adams'),
    ('denver_general', 'Suspected pulmonary embolism', 'CTA chest with IV contrast',
     'Usually appropriate', 'dg_ct_chest_pe', 0.95, 'manual_review', 'Dr. Adams'),
    ('denver_general', 'Acute dyspnea', 'CTA chest with IV contrast',
     'Usually appropriate', 'dg_ct_chest_pe', 0.80, 'manual_review', 'Dr. Adams'),
    ('denver_general', 'Headache', 'MRI head without and with IV contrast',
     'Usually appropriate', 'dg_mri_brain_wwoc', 0.90, 'manual_review', 'Dr. Adams'),
    ('denver_general', 'New intracranial lesion', 'MRI head without and with IV contrast',
     'Usually appropriate', 'dg_mri_brain_wwoc', 0.95, 'manual_review', 'Dr. Adams'),
    ('denver_general', 'Right upper quadrant pain', 'US abdomen',
     'Usually appropriate', 'dg_us_ruq', 0.90, 'manual_review', 'Dr. Adams');

