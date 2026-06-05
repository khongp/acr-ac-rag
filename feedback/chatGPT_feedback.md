# Strategic Review of the ACR-AC-RAG Codebase

## Executive Summary

After reviewing the current ACR-AC-RAG architecture and comparing it to the official American College of Radiology (ACR) Clinical Decision Support (CDS) ecosystem—including ACR Appropriateness Criteria (AC), ACR Select, and Optum CareSelect Imaging—I do **not** believe the project should be abandoned.

However, I do believe the current platform attempts to solve too many adjacent problems simultaneously, which obscures its strongest differentiators and makes it difficult to communicate a clear product identity.

The purpose of this review is to identify:

1. Where the current platform overlaps with existing ACR/CDS solutions.
2. Where meaningful differentiation already exists.
3. Which areas should be emphasized architecturally and strategically.
4. Which areas may represent unnecessary complexity.

---

# Comparison to Existing ACR CDS Systems

## What Existing ACR CDS Platforms Primarily Do

Current ACR CDS products are designed around imaging order validation.

Typical workflow:

1. Ordering clinician enters an imaging order.
2. CDS evaluates the order against ACR Appropriateness Criteria.
3. System assigns an appropriateness score.
4. Alternative exams may be suggested.
5. Compliance data are captured for reporting and quality initiatives.

The core question these systems answer is:

> "Is this imaging order appropriate according to ACR guidelines?"

These systems are primarily compliance, governance, and ordering-support tools.

---

# What the Current ACR-AC-RAG System Actually Does

The current architecture extends significantly beyond appropriateness scoring.

## 1. Clinical Narrative → Structured Reasoning

The platform begins with unstructured clinical text.

Example:

> "68-year-old female with CKD stage 4, contrast allergy, worsening headaches."

The system extracts structured information and maps it into FHIR resources.

This is fundamentally different from validating an already-selected imaging order.

Current CDS systems generally assume an order already exists.

The current platform reasons before the order is finalized.

---

## 2. Safety-Aware Imaging Recommendation

The architecture incorporates patient-specific constraints including:

* Renal function
* Contrast allergies
* Pregnancy status
* Anticoagulation
* Procedure-specific contraindications
* Medication-related considerations

This moves beyond guideline retrieval into clinical safety support.

Current ACR implementations generally do not provide comprehensive safety reasoning layers of this type.

---

## 3. Alternative Modality Discovery

One of the most interesting architectural components is the iterative re-query mechanism.

Example:

1. MRI recommended by ACR.
2. MRI contraindicated due to implanted device.
3. System automatically searches for acceptable alternatives.

This represents dynamic reasoning rather than static guideline lookup.

---

## 4. Protocoling Intelligence

This appears to be one of the strongest differentiators in the entire codebase.

Most CDS systems stop at:

> "CT Abdomen/Pelvis with IV Contrast"

Radiology departments still must determine:

* Protocol selection
* Contrast strategy
* Imaging phases
* Timing
* Institution-specific implementations

The protocol database layer begins addressing operational radiology workflow rather than simply imaging appropriateness.

This occupies a different product category than traditional CDS.

---

## 5. Conversational Attending / Copilot Functionality

The platform allows users to ask:

> "Why is MRI preferred here?"

or

> "What factors drove this recommendation?"

This creates an educational and explainable interface to the ACR criteria.

Traditional CDS products are not optimized for conversational reasoning or resident education.

---

# Major Architectural Concern

## Scope Creep

The current README suggests the platform simultaneously attempts to provide:

* ACR retrieval
* RAG infrastructure
* FHIR extraction
* CDS Hooks integration
* Safety engine
* Protocol recommendation
* Override auditing
* DSN generation
* Review workflows
* Conversational assistant

These represent multiple independent products.

As a result, the system risks becoming difficult to explain to:

* Radiologists
* Department leaders
* Potential institutional partners
* Investors
* Developers

A common reaction may be:

> "What exactly is the product?"

---

# Strongest Product Opportunity

If forced to choose a single direction, the strongest opportunity appears to be:

## AI Radiology Protocoling Copilot

### Input

* Free-text indication
* Clinical history
* Relevant patient factors

### Output

* ACR recommendation
* Recommended imaging study
* Protocol recommendation
* Contrast recommendation
* Safety considerations
* Institution-specific protocol mapping

This workflow aligns directly with real radiology operational pain points.

---

# Most Defensible Differentiator

The strongest differentiator identified is:

## Local Protocol Intelligence

The ACR can answer:

> "Which study is appropriate?"

It generally cannot answer:

> "How does Hospital X actually perform this study?"

Examples:

* Local protocol names
* Sequence selection
* Contrast dosing
* Timing preferences
* Institutional workflow variations

This knowledge typically exists only within individual departments.

The current protocol database architecture creates a bridge between:

National Guidelines
↓
Institutional Practice
↓
Operational Execution

This is potentially much more valuable than another appropriateness checker.

---

# Market Reality

Competing directly against established CDS vendors as an appropriateness engine would be difficult.

Existing organizations may already have:

* CareSelect Imaging
* Epic-integrated CDS
* Internal imaging pathways
* Existing governance workflows

Therefore, competing solely on:

> "I can retrieve ACR recommendations."

is unlikely to create a durable advantage.

Competing on:

> "I can operationalize those recommendations into protocoling and workflow decisions."

is significantly more compelling.

---

# Recommended Strategic Repositioning

Current branding:

> ACR-AC-RAG

Recommended positioning:

> Radiology Protocoling Copilot

or

> AI Imaging Protocol Assistant

or

> Radiology Workflow Intelligence Platform

This better reflects the unique value proposition.

---

# Recommended Engineering Priorities

## Continue Investing In

### High Priority

* Protocol recommendation engine
* Safety reasoning layer
* Local protocol database
* Institution-specific customization
* Explainable recommendations
* FHIR integration
* Clinical narrative extraction

### Medium Priority

* Override tracking
* Quality analytics
* Audit workflows

### Lower Priority

* Generic ACR retrieval features that already exist elsewhere
* Functionality that simply replicates CareSelect behavior

---

# Final Assessment

The project should not be abandoned.

The codebase already contains functionality that extends beyond traditional ACR CDS systems.

The greatest opportunity is not replacing ACR appropriateness criteria.

The greatest opportunity is solving the last-mile problem between:

Clinical Indication
→ Appropriate Study
→ Safe Study
→ Correct Protocol
→ Institution-Specific Execution

That workflow remains fragmented in many radiology departments and appears to be the area where the current architecture demonstrates the strongest differentiation and long-term potential.
