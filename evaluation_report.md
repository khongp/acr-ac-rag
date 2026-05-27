# ACR-AC-RAG Batch Validation Report

Generated on: 2026-05-24 14:22:34

### Performance Summary
- **Total Cases Evaluated**: 11
- **Passed Cases (Topic Matched in Top 3)**: 8
- **Failed Cases**: 3
- **Retrieval Accuracy**: **72.73%**

## Detailed Test Cases

### 1. Major Blunt Trauma - 🔴 FAIL
- **Original ACR Scenario**: Major blunt trauma, hemodynamically stable, facial injury suspected, initial imaging
- **Generated Simulated Query**: *"45-year-old man presenting after a high-speed MVC, hemodynamically stable, with significant facial pain and swelling. What initial imaging is recommended?"*
- **FHIR Extracted Scenario**: `"45-year-old male with high-speed motor vehicle collision, hemodynamically stable, significant facial pain and swelling, major blunt trauma, facial trauma."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Imaging of Facial Trauma Following Primary Survey`, **Scenario**: `trismus, mandibular fracture suspected, initial imaging following primary survey`
  2. **Topic**: `Imaging of Facial Trauma Following Primary Survey`, **Scenario**: `facial elongation, midface fracture suspected,  initial imaging following primary survey`
  3. **Topic**: `Imaging of Facial Trauma Following Primary Survey`, **Scenario**: `loose teeth, mandibular fracture suspected, initial imaging following primary survey`

---

### 2. Head Trauma - 🔴 FAIL
- **Original ACR Scenario**: Head trauma, intracranial arterial injury suspected, risk factors
- **Generated Simulated Query**: *"35yo male status post high-impact head trauma, now with worsening headache and signs of evolving stroke, prompting concern for intracranial arterial dissection."*
- **FHIR Extracted Scenario**: `"35-year-old male with high-impact head trauma, worsening headache, signs of evolving stroke, concern for intracranial arterial dissection, head trauma, stroke."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Cerebrovascular Diseases-Stroke and Stroke-Related Conditions`, **Scenario**: `cervical vascular dissection suspected, initial imaging`
  2. **Topic**: `Cerebrovascular Diseases-Stroke and Stroke-Related Conditions`, **Scenario**: `cervical vascular injury, surveillance`
  3. **Topic**: `Cerebrovascular Disease-Child`, **Scenario**: `cervicocranial arterial dissection on imaging or clinical exam, next imaging study`

---

### 3. Low Back Pain - 🟢 PASS (Rank 2)
- **Original ACR Scenario**: Low back pain, chronic, intervention candidate, persistent symptoms after 6 wks medical management, initial imaging
- **Generated Simulated Query**: *"62-year-old female with chronic low back pain, persistent despite 6 weeks of conservative therapy. What initial imaging is appropriate for considering further interventional options?"*
- **FHIR Extracted Scenario**: `"62-year-old female with chronic low back pain, low back pain."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Chronic Hip Pain`, **Scenario**: `hip pain, chronic, low back pain, hip osteoarthritis on radiography, next imaging study, quantify pain from hip pathology`
  2. **Topic**: `Low Back Pain`, **Scenario**: `low back pain, elderly, initial imaging` **(MATCHED)**
  3. **Topic**: `Low Back Pain`, **Scenario**: `low back pain, osteoporosis, initial imaging` **(MATCHED)**

---

### 4. Acute Hip Pain - 🟢 PASS (Rank 1)
- **Original ACR Scenario**: Hip pain, acute, traumatic, initial imaging
- **Generated Simulated Query**: *"72yo female presents with acute left hip pain after a fall. What imaging should I order?"*
- **FHIR Extracted Scenario**: `"72-year-old female with acute left hip pain, fall, hip trauma."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Acute Hip Pain`, **Scenario**: `hip pain, acute, traumatic, initial imaging` **(MATCHED)**
  2. **Topic**: `Acute Hip Pain`, **Scenario**: `hip pain, acute, traumatic, fracture on radiography, next imaging study` **(MATCHED)**
  3. **Topic**: `Acute Hip Pain`, **Scenario**: `hip pain, acute, traumatic, fracture suspected, radiography indeterminate, next imaging study` **(MATCHED)**

---

### 5. Rib Fractures - 🟢 PASS (Rank 1)
- **Original ACR Scenario**: Chest trauma, blunt, rib fracture suspected
- **Generated Simulated Query**: *"40-year-old woman with acute chest pain and tenderness after a fall, worse with inspiration. Concerned for rib fracture."*
- **FHIR Extracted Scenario**: `"40-year-old female with acute chest pain, tenderness after a fall, worse with inspiration, concerned for rib fracture, chest trauma."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Rib Fractures`, **Scenario**: `rib fracture suspected, after cpr` **(MATCHED)**
  2. **Topic**: `Chest Pain-Child`, **Scenario**: `chest wall pain, initial imaging`
  3. **Topic**: `Rib Fractures`, **Scenario**: `chest trauma, blunt, rib fracture suspected` **(MATCHED)**

---

### 6. Acute Spinal Trauma - 🟢 PASS (Rank 1)
- **Original ACR Scenario**: Lumbar spine trauma, acute, blunt, nerve root injury, with or without trauma on CT, next imaging study
- **Generated Simulated Query**: *"67yo female with acute low back pain and unilateral leg weakness after a fall. CT negative for fracture. What's the next best imaging for suspected lumbar radiculopathy?"*
- **FHIR Extracted Scenario**: `"67-year-old female with acute low back pain, unilateral leg weakness, fall, suspected lumbar radiculopathy, low back pain."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Acute Spinal Trauma`, **Scenario**: `lumbar spine trauma, acute, blunt, spinal cord injury, with or without trauma on ct, next imaging study` **(MATCHED)**
  2. **Topic**: `Low Back Pain`, **Scenario**: `low back pain, elderly, initial imaging`
  3. **Topic**: `Acute Spinal Trauma`, **Scenario**: `lumbar spine trauma, acute, blunt, >60yo, mechanism consistent with thoracolumbar spine injury, initial imaging` **(MATCHED)**

---

### 7. Suspected Spine Infection - 🟢 PASS (Rank 1)
- **Original ACR Scenario**: Spine infection suspected, cervical and thoracic, CT abnormal, next imaging study
- **Generated Simulated Query**: *"67yo female presenting with acute neck and thoracic back pain and fevers. Prior CT spine showed abnormalities concerning for infection. What is the next recommended imaging study?"*
- **FHIR Extracted Scenario**: `"67-year-old female with acute neck pain, acute thoracic back pain, fevers, spinal infection."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Suspected Spine Infection`, **Scenario**: `spine infection suspected, thoracic, radiography abnormal, next imaging study` **(MATCHED)**
  2. **Topic**: `Suspected Spine Infection`, **Scenario**: `spine infection suspected, cervical, radiography abnormal, next imaging study` **(MATCHED)**
  3. **Topic**: `Suspected Spine Infection`, **Scenario**: `spine infection suspected, cervical and thoracic, radiography abnormal, next imaging study` **(MATCHED)**

---

### 8. Renovascular Hypertension - 🟢 PASS (Rank 1)
- **Original ACR Scenario**: HTN, high suspicion renal vascular, decreased renal function
- **Generated Simulated Query**: *"68-year-old man with refractory hypertension and worsening kidney function, concerned for renovascular etiology."*
- **FHIR Extracted Scenario**: `"68-year-old male with refractory hypertension, worsening kidney function, renovascular etiology."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Renovascular Hypertension`, **Scenario**: `htn, high suspicion renal vascular, decreased renal function` **(MATCHED)**
  2. **Topic**: `Renovascular Hypertension`, **Scenario**: `htn, high suspicion renal vascular, normal renal function` **(MATCHED)**
  3. **Topic**: `Nonatherosclerotic Peripheral Arterial Disease`, **Scenario**: `fibromuscular dysplasia or other noninflammatory vascular disease`

---

### 9. Jaundice - 🟢 PASS (Rank 1)
- **Original ACR Scenario**: Jaundice, biliary obstruction suspected
- **Generated Simulated Query**: *"67-year-old female presenting with new-onset yellow skin and eyes, dark urine, and pruritus. Suspect biliary obstruction."*
- **FHIR Extracted Scenario**: `"67-year-old female with New-onset yellow skin and eyes, dark urine, and pruritus. Suspect biliary obstruction.."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Jaundice`, **Scenario**: `jaundice, biliary obstruction suspected` **(MATCHED)**
  2. **Topic**: `Jaundice`, **Scenario**: `jaundice, non-obstructive etiology suspected` **(MATCHED)**
  3. **Topic**: `Abnormal Liver Function Tests`, **Scenario**: `abnormal liver function tests, hyperbilirubinemia, subacute cholestasis, unconjugated, initial imaging`

---

### 10. Hematuria - 🔴 FAIL
- **Original ACR Scenario**: Hematuria, microscopic, risk factors, no recent vigorous exercise, initial imaging
- **Generated Simulated Query**: *"65-year-old male with a smoking history presents with asymptomatic microscopic blood in his urine on routine labs, denying recent strenuous activity. What initial imaging is recommended?"*
- **FHIR Extracted Scenario**: `"65-year-old male with asymptomatic microscopic hematuria, smoking history."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Post-Treatment Surveillance of Bladder Cancer`, **Scenario**: `bladder cancer, nonmuscle invasive, treated, asymptomatic, surveillance`
  2. **Topic**: `Post-Treatment Surveillance of Bladder Cancer`, **Scenario**: `bladder cancer, nonmuscle, invasive, treated, risk factors, surveillance`
  3. **Topic**: `Post-Treatment Surveillance of Bladder Cancer`, **Scenario**: `bladder cancer, nonmuscle invasive, treated, no risk factors, surveillance`

---

### 11. Dementia - 🟢 PASS (Rank 1)
- **Original ACR Scenario**: Cognitive impairment, memory deficits, Alzheimer disease suspected, typical clinical presentation, initial imaging
- **Generated Simulated Query**: *"Initial imaging recommendations for a 72yo female with progressive memory loss and cognitive decline, suspected Alzheimer's disease."*
- **FHIR Extracted Scenario**: `"72-year-old female with progressive memory loss and cognitive decline, suspected Alzheimer's disease, cognitive decline."`
- **Retrieved Top 3 Candidates**:
  1. **Topic**: `Dementia`, **Scenario**: `cognitive impairment, memory deficits, alzheimer disease suspected, atypical clinical presentation, initial imaging` **(MATCHED)**
  2. **Topic**: `Dementia`, **Scenario**: `cognitive impairment, memory deficits, alzheimer disease suspected, typical clinical presentation, initial imaging` **(MATCHED)**
  3. **Topic**: `Dementia`, **Scenario**: `cognitive impairment, stepwise decline, vascular dementia suspected, initial imaging` **(MATCHED)**

---

