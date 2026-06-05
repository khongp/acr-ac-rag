# ACR-AC-RAG Validation & Testing Framework

This document outlines the strategic roadmap for validating the clinical accuracy and safety of the `ACR-AC-RAG` pipeline. Use this guide to implement automated evaluation scripts and establish a ground-truth testing methodology.

## Phase 1: Establish a "Golden Dataset" (Ground Truth)

Select 3 to 5 high-volume clinical scenarios (e.g., *Acute Low Back Pain*, *Suspected Pulmonary Embolism*, and an *Interventional Radiology* pathway) to serve as the baseline.

1. **Extract the Ground Truth:** Map out the exact official pairings from the ACR guidelines: `Clinical Scenario` -> `Recommended Exam` -> `Appropriateness Score (1-9)`.
2. **Generate "Messy" Clinical Queries:** For each scenario, write 5 different variations mimicking real-world clinical notes.
    * *Official:* "Acute low back pain with radiculopathy."
    * *Variation 1:* "54M tweaked his back lifting a couch, now has shooting pain down the left leg and tingling in the toes."
    * *Variation 2:* "Severe LBP + radicular symptoms, rule out disc herniation."
    * *Variation 3 (With Noise):* "Patient missed their last appointment, presents today with worsening lower back ache radiating to thigh. Denies bowel/bladder incontinence."

**Goal:** Create a starting pool of 50 to 100 test queries mapped to known ACR appropriateness scores.

## Phase 2: Build an Automated Evaluation Script

Leverage the existing `pytest` structure and async capabilities to build a dedicated evaluation script (e.g., `test_rag_accuracy.py`). This script will programmatically hit the FastAPI backend endpoints using the Golden Dataset.

Implement tracking for three core metrics per query:
* **Top-1 Match Rate:** Did the `ChromaDB` + `Gemini Reranker` pipeline return the correct guideline topic as the top result?
* **Score Alignment:** Does the clinical variant selected by the app carry the exact same appropriateness rating (e.g., Score 9) as the official ACR table?
* **Safety Trigger Accuracy:** When a messy query contains contraindications (e.g., "history of anaphylaxis to Omnipaque" or "eGFR of 28"), verify that `safety_engine.py` successfully intercepts the order, flags the contrast warning, or triggers a closed-loop re-query.

## Phase 3: Stress-Test the "Abstention Gate"

Verify that the system safely handles highly ambiguous inputs or non-clinical garbage text. 

* **Test Inputs:** Feed the system irrelevant or nonsensical strings (e.g., `"patient wants a grilled cheese sandwich"`, `"left big toe clicking sounds"`).
* **Expected Behavior:** Ensure the backend correctly flags these queries with a confidence score below `0.55` and routes them to the manual review queue (`data/query_cache.db`) instead of hallucinating a radiology protocol.

## Phase 4: Qualitative Edge-Case Testing

Once automated accuracy is high (>90%), conduct hands-on testing via the web dashboard (`index.html`).

* Have colleagues input live, unstructured test cases.
* Monitor where the regex parser or Gemini entity extraction fails to recognize specific clinical shorthand or niche acronyms.
* Update local abbreviation expansion mappings based on these failures to continuously improve the semantic parser.
