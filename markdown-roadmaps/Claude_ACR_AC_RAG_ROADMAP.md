# ACR-AC-RAG: Engineering Roadmap & Product Superiority Plan
**Version:** 2.1.0 → Target 3.0.0  
**Last Updated:** May 2026  
**Status:** Active Development

---

## Table of Contents

1. [Current System Audit](#1-current-system-audit)
2. [Competitive Landscape](#2-competitive-landscape)
3. [Critical Gaps in v2.1.0](#3-critical-gaps-in-v210)
4. [Pre-Work: Build Your Eval Framework First](#4-pre-work-build-your-eval-framework-first)
5. [Phase 1 — Core Safety & Retrieval (Months 1–3)](#5-phase-1--core-safety--retrieval-months-13)
6. [Phase 2 — Cognitive Safeguards & Hybrid Deployment (Months 4–6)](#6-phase-2--cognitive-safeguards--hybrid-deployment-months-46)
7. [Phase 3 — SMART on FHIR & EHR Embedding (Months 7–9)](#7-phase-3--smart-on-fhir--ehr-embedding-months-79)
8. [Phase 4 — RL, Governance & qCDSM Compliance (Months 10–12)](#8-phase-4--rl-governance--qcdsm-compliance-months-1012)
9. [Architecture Diagrams](#9-architecture-diagrams)
10. [File-by-File Refactor Notes](#10-file-by-file-refactor-notes)
11. [Data Schema Reference](#11-data-schema-reference)
12. [Key Dependencies & Versions](#12-key-dependencies--versions)

---

## 1. Current System Audit

### What You Have (v2.1.0 Strengths)

| Module | Status | Notes |
|---|---|---|
| `fhir_converter.py` | ✅ Strong | Converts unstructured text → FHIR Bundle (Patient, Condition, Observation, AllergyIntolerance, MedicationStatement) |
| `rag_engine.py` | ⚠️ Needs upgrade | Single-vector ChromaDB retrieval; no hybrid search |
| `safety_engine.py` | ✅ Very strong | eGFR gating, allergy cross-ref, hCG pregnancy, med holds — **unique competitive moat** |
| `protocol_mapper.py` | ✅ Strong scaffold | ACR → local hospital protocol mapping; needs self-learning layer |
| `protocol_db.py` | ✅ Good | SQLite schema for scanner/protocol/contrast rules |
| `copilot_engine.py` | ✅ Good | Gemini-based attending radiologist co-pilot |
| `medical_ontology.py` | ✅ Good | RxNorm, LOINC, SNOMED-CT mappings for meds and contrast agents |
| `main.py` | ⚠️ Needs additions | Missing abstention logic, DSN logging, PHI audit trail |
| `ingest.py` | ⚠️ Needs review | Chunking strategy likely naive; needs semantic chunking audit |
| `fix_bm25.py` | ✅ Already scaffolded | BM25 retriever exists — **activate hybrid search immediately** |
| `index.html` | ✅ Good | Responsive dashboard; needs SMART on FHIR wrapper later |
| `app.py` | ⚠️ Phase 1 only | Streamlit MVP; replace with embedded SMART on FHIR app |

### Core Architecture (Current)

```
Clinical Text Input
        │
        ▼
┌──────────────────┐
│  fhir_converter  │  → FHIR Bundle
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   rag_engine     │  → ChromaDB vector search (single-vector, naive)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ protocol_mapper  │  → SQLite ACR → local protocol lookup
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  safety_engine   │  → eGFR / allergy / pregnancy / med hold checks
└────────┬─────────┘
         │
         ▼
     CDS Card Output (FastAPI /v1/cds-hook)
```

---

## 2. Competitive Landscape

### Academic Systems

#### 2.1 accGPT (Rau et al., 2023)
- **Approach:** LlamaIndex + GPT-3.5-Turbo; indexes 209 ACR guidelines; takes raw clinical referral notes
- **Performance:** 83% "usually appropriate" rate; outperforms human general radiologists (66%); Fleiss κ = 0.82; 8 min vs. 50 min human decision time
- **Pros:** Source-verified RAG chunks; dramatic time savings vs. manual search
- **Cons:** Single-vector index (text-embedding-ada-002); naive text chunking; no EHR/lab integration; no contraindication checking; no local protocol mapping
- **What your system does better:** `safety_engine.py`, `protocol_mapper.py`, FHIR-native intake — all completely absent from accGPT

#### 2.2 ped-Llama (Gupta et al., 2025)
- **Approach:** Locally deployed Llama-3.1-8B + pediatric-scoped ACR-AC RAG vector DB
- **Performance:** 80–90% accuracy on pediatric cases; matches pediatric radiology specialists (76%); beats GPT-4o (54%) and Claude Opus (46%) on peds
- **Pros:** Zero PHI leakage (fully on-premises); excellent pediatric clinical variation handling
- **Cons:** Requires significant GPU infrastructure per site; 8B model struggles with multi-hop reasoning; prompt-sensitive; not generalizable beyond peds
- **What your system does better:** Full safety rule engine; FHIR compliance; adult + peds scope
- **What you need to copy:** On-premises deployment option (Phase 2)

#### 2.3 MSK MRI Protocoling LLM (Lee et al., 2026)
- **Approach:** GPT-4o for sequence selection, FOV, metal/implant artifact handling in MSK MRI orders
- **Performance:** LLM-assisted scores higher on 4-point clinical pass system (Residents: 3.42 vs. 3.18); 12.2% reduction in protocoling errors; lower repeat scan rates
- **Pros:** Directly targets the operational bottleneck — local protocol selection, not just modality recommendation
- **Cons:** No pipeline automation; manual web-interface workflow; not production-grade
- **What your system does better:** Your `protocol_mapper.py` automates exactly what this system does manually

#### 2.4 Multi-Agent ColBERT RAG (Pambudi & Menolascina, 2025)
- **Approach:** ColBERT late-interaction retrieval fine-tuned on 8,840 synthetic clinical scenario-recommendation pairs + GPT-4.1/MedGemma agents
- **Performance:** 93.9% top-10 retrieval recall; 81% exact match accuracy; +67 percentage points over standalone GPT-4.1
- **Pros:** Token-level matching resolves lexically divergent clinical notes; multi-agent task separation improves factual reasoning
- **Cons:** Very high computational overhead; multi-agent loop latency impractical for ED workflows
- **What you need to copy:** ColBERT re-ranking layer on top of ChromaDB (Phase 1); full multi-agent architecture (Phase 1)

#### 2.5 Optimized MSK RAG (Tan et al., 2025)
- **Approach:** GPT-4-based RAG with Chain-of-Thought prompting on 33 ACR-AC MSK guidelines
- **Performance:** 92.86% clinical decision accuracy vs. standard GPT-4 (51.29%) and baseline RAG (61.43%)
- **Pros:** Solves "negative rejection" — refuses to recommend when input is clinically insufficient, preventing hallucinated orders
- **Cons:** Narrow scope (MSK MRI only); expensive multi-step CoT token usage
- **What you need to copy:** Abstention gate / negative rejection engine (Phase 1 — critical)

#### 2.6 Mayo Clinic Protocoling RAG (Testagrose, 2026)
- **Approach:** Llama 3.2 3B + FAISS vector indexes from localized procedure and diagnostic text; multi-site study (AZ, FL, MN)
- **Performance:** Improved Macro F1 at AZ (Δ=+0.0306) and FL (Δ=+0.0245); 1–2.5% abstention routing for ambiguous cases
- **Pros:** Real-world multi-site validation; easy index refresh without retraining; effective abstention routing
- **Cons:** Site-specific heterogeneity caused RAG to hurt performance at Rochester (Δ=-0.0180) — proves retrieval breaks on non-standard local naming conventions
- **What you need to copy:** Abstention routing to human review queue; versioned guideline indexes

### Commercial System

#### 2.7 CareSelect Imaging (NovRx/NovuHealth — Current Market Incumbent)
- **Integration:** Native to Epic, Cerner, MEDITECH; PAMA-compliant; generates DSNs and billing G-codes
- **Pros:** Regulatory compliance; EHR-embedded; insurance reimbursement workflows
- **Cons:**
  - Severe clinician alert fatigue
  - Manual structured input via dropdown menus only — cannot parse unstructured clinical narratives
  - No dynamic local protocol mapping
  - No patient-level laboratory evaluation
  - No safety rule engine (eGFR, allergies, pregnancy)
  - Rigid rule-sets with no LLM reasoning
- **Your competitive opening:** You can parse free text, evaluate patient-level safety, map to local protocols, AND generate DSN-compliant output — all in one pipeline

---

## 3. Critical Gaps in v2.1.0

Listed in order of clinical risk and implementation priority.

### Gap 1 — No Abstention / Negative Rejection Logic (🔴 Critical)
**Problem:** If a clinician inputs "hip pain," your API generates a recommendation regardless of how sparse the input is. This can produce clinically unsafe or hallucinated orders.  
**Risk:** Patient safety incident; regulatory liability  
**Fix:** Phase 1 — Abstention gate in `rag_engine.py` and `main.py`

### Gap 2 — Flat Single-Vector Retrieval (🟠 High)
**Problem:** Standard ChromaDB cosine similarity fails on lexically divergent clinical notes (e.g., "thunderclap HA" vs. "sudden-onset severe headache" vs. "worst headache of life" — all the same presentation, all may retrieve different chunks).  
**Risk:** Wrong or incomplete guideline retrieval → bad recommendations  
**Fix:** Phase 1 — Hybrid BM25 + vector (already scaffolded in `fix_bm25.py`) then ColBERT re-ranking

### Gap 3 — Poor Chunking Strategy in `ingest.py` (🟠 High)
**Problem:** Naive fixed-size chunking splits ACR variant table rows across chunk boundaries, destroying semantic units before they even reach the retriever.  
**Risk:** Garbage in, garbage out — no retrieval algorithm fixes bad chunks  
**Fix:** Phase 1 — Semantic + hierarchical chunking in `ingest.py`

### Gap 4 — No Evaluation Framework (🟠 High)
**Problem:** There is no ground truth test set and no automated metrics. You cannot measure if a code change improves or degrades performance.  
**Risk:** Silent regressions ship to production  
**Fix:** Pre-work — build eval set before any other changes

### Gap 5 — Safety Engine → RAG Feedback Loop Missing (🟡 Medium)
**Problem:** Architecture is linear: RAG → Safety → output. If Safety flags a hard contraindication (e.g., iodinated contrast + severe allergy + eGFR < 30), the system surfaces a warning but does NOT automatically re-query RAG for non-contrast alternatives.  
**Risk:** Clinician gets a warning card with no actionable alternative path  
**Fix:** Phase 1 — Closed-loop re-query in `main.py`

### Gap 6 — No Guideline Version Control (🟡 Medium)
**Problem:** ACR updates criteria periodically. Current system has no mechanism to track which version of a guideline sourced a recommendation, nor to do incremental re-ingestion without full DB rebuild.  
**Risk:** Stale recommendations; audit trail failure for qCDSM compliance  
**Fix:** Phase 1 — Version metadata in ChromaDB + `ingest.py`

### Gap 7 — Cloud-Only Gemini Dependency (🟡 Medium)
**Problem:** HIPAA-sensitive health systems often cannot sign a BAA for a system with mandatory cloud LLM calls. Blocks sales to on-premises-first hospitals.  
**Risk:** Large market segment inaccessible  
**Fix:** Phase 2 — Docker + Ollama/llama.cpp local fallback mode

### Gap 8 — No Pediatric Protocol Branching (🟡 Medium)
**Problem:** ped-Llama showed general-purpose frontier LLMs score only 54% on pediatric cases. Your system has no explicit pediatric branch — no age-gated retrieval context, no pediatric-specific RRL weighting.  
**Risk:** Pediatric recommendations sourced from adult guideline chunks  
**Fix:** Phase 2 — Age detection in `fhir_converter.py` → pediatric-scoped retrieval

### Gap 9 — No DSN/G-code Generation (🟡 Medium)
**Problem:** Without Decision Support Numbers and billing G-codes, you cannot participate in CMS workflows. This is how CareSelect maintains hospital contracts.  
**Risk:** Cannot compete for enterprise contracts that require PAMA compliance  
**Fix:** Phase 4 — DSN audit log in `main.py`

### Gap 10 — No Clinician Override Capture (🟢 Low/Long-term)
**Problem:** When a clinician ignores your recommendation and orders something else, that signal is lost. This is your highest-value training data.  
**Risk:** No feedback flywheel; model cannot improve from real-world usage  
**Fix:** Phase 1 — Override logging endpoint (low effort, high long-term value)

### Gap 11 — No Prompt Injection Hardening (🟢 Low)
**Problem:** API accepts free clinical text and passes it directly to Gemini. A malicious or misconfigured EHR payload could inject instructions into your prompt.  
**Fix:** Phase 1 — Input sanitization layer in `copilot_engine.py`

---

## 4. Pre-Work: Build Your Eval Framework First

> **Do this before writing any Phase 1 code. Without it, you are flying blind.**

### 4.1 Ground Truth Test Set

Build a CSV with 75–100 rows manually validated by a radiologist:

```csv
scenario_text,expected_modality,expected_appropriateness,expected_variant_id,notes
"70yo male sudden worst headache of life no focal neuro deficit","CT Head without contrast","Usually Appropriate","Headache_3","Classic thunderclap HA"
"45yo female right hip pain 3 months no trauma hx osteoarthritis","X-Ray Hip","Usually Appropriate","MSK_Hip_1",""
"55yo male CKD3 eGFR 32 suspected PE hemodynamically stable","CT Pulmonary Angiography","Usually Appropriate","Chest_PE_2","Safety engine should also flag eGFR warning"
...
```

### 4.2 Retrieval Metrics (Separate from Generation)

Track these independently so you know whether a regression is a retrieval problem or a generation problem:

```python
# metrics/eval.py

def mean_reciprocal_rank(results: list[list[str]], ground_truth: list[str]) -> float:
    """MRR — did the correct guideline appear, and how high?"""
    scores = []
    for retrieved, correct in zip(results, ground_truth):
        for rank, doc_id in enumerate(retrieved, start=1):
            if doc_id == correct:
                scores.append(1 / rank)
                break
        else:
            scores.append(0)
    return sum(scores) / len(scores)

def recall_at_k(results: list[list[str]], ground_truth: list[str], k: int = 5) -> float:
    """Recall@K — is the correct guideline in the top K results?"""
    hits = sum(1 for retrieved, correct in zip(results, ground_truth) 
               if correct in retrieved[:k])
    return hits / len(ground_truth)
```

### 4.3 Generation Metrics

```python
# metrics/eval.py (continued)

def appropriateness_accuracy(predictions: list[str], ground_truth: list[str]) -> float:
    """Exact match on appropriateness category."""
    correct = sum(1 for p, g in zip(predictions, ground_truth) 
                  if p.strip().lower() == g.strip().lower())
    return correct / len(ground_truth)

def abstention_rate(predictions: list[dict]) -> float:
    """% of cases where the system correctly withheld a recommendation."""
    abstentions = sum(1 for p in predictions if p.get("abstained", False))
    return abstentions / len(predictions)
```

### 4.4 Run Eval in CI

Add a GitHub Actions step that runs your eval suite on every PR:

```yaml
# .github/workflows/eval.yml
- name: Run RAG Eval Suite
  run: python metrics/run_eval.py --test-set data/eval/ground_truth.csv --report metrics/eval_report.json

- name: Check Accuracy Regression
  run: python metrics/check_regression.py --report metrics/eval_report.json --min-accuracy 0.80
```

---

## 5. Phase 1 — Core Safety & Retrieval (Months 1–3)

### 5.1 Fix Chunking in `ingest.py` (Week 1)

> This is upstream of everything. Bad chunks cannot be fixed by better retrieval.

**Current problem:** Naive fixed-size character/token chunking splits ACR variant rows mid-sentence.

**Target structure:**

```
Level 0: Topic summary (1 chunk per ACR topic — e.g., "Headache")
  └── Level 1: Variant scenario (1 chunk per variant — e.g., "Thunderclap headache, first episode")
        └── Level 2: Procedure entries (1 chunk per procedure row with full context inherited)
```

**Implementation in `ingest.py`:**

```python
def build_hierarchical_chunks(topic: dict) -> list[Document]:
    """
    Build 3-level hierarchical chunks from a parsed ACR topic dict.
    Each Level 2 chunk inherits full context from Levels 0 and 1
    so retrieval always returns a self-contained, contextualized unit.
    """
    chunks = []
    topic_name = topic["topicName"]
    topic_id = topic["topicId"]
    guideline_version = topic.get("version", "unknown")
    last_updated = topic.get("lastUpdated", "unknown")
    
    # Group variants
    variants: dict[str, list] = {}
    for row in topic.get("variantData", []):
        scenario = row.get("Scenario", "")
        variants.setdefault(scenario, []).append(row)
    
    for scenario, procedures in variants.items():
        # Level 1: Variant summary chunk
        procedure_list = "\n".join(
            f"- {p['Procedure']} | {p['Appropriateness Category']} | Adult RRL: {p['Adult RRL']}"
            for p in procedures
        )
        variant_text = (
            f"ACR Topic: {topic_name}\n"
            f"Clinical Scenario: {scenario}\n\n"
            f"Recommended Procedures:\n{procedure_list}"
        )
        chunks.append(Document(
            page_content=variant_text,
            metadata={
                "topic_id": topic_id,
                "topic_name": topic_name,
                "scenario": scenario,
                "level": "variant_summary",
                "guideline_version": guideline_version,
                "last_updated": last_updated,
                "chunk_type": "structured_table",
            }
        ))
        
        # Level 2: Per-procedure chunks (full context inherited)
        for proc in procedures:
            proc_text = (
                f"ACR Topic: {topic_name}\n"
                f"Clinical Scenario: {scenario}\n"
                f"Procedure: {proc['Procedure']}\n"
                f"Appropriateness: {proc['Appropriateness Category']}\n"
                f"Adult Radiation Level (RRL): {proc['Adult RRL']}\n"
                f"Pediatric Radiation Level (RRL): {proc['Peds RRL']}"
            )
            chunks.append(Document(
                page_content=proc_text,
                metadata={
                    "topic_id": topic_id,
                    "topic_name": topic_name,
                    "scenario": scenario,
                    "procedure": proc["Procedure"],
                    "appropriateness": proc["Appropriateness Category"],
                    "adult_rrl": proc["Adult RRL"],
                    "peds_rrl": proc["Peds RRL"],
                    "level": "procedure",
                    "guideline_version": guideline_version,
                    "last_updated": last_updated,
                    "chunk_type": "structured_table",
                }
            ))
    
    return chunks
```

**Also tag PDF narrative chunks** with guideline version and topic ID so they can be filtered/expired:

```python
def chunk_narrative_pdf(pdf_path: str, topic_id: int, version: str) -> list[Document]:
    """Semantic chunking of PDF narratives — split on paragraph/section boundaries, not token count."""
    # Use pypdf to extract text, then split on double-newline paragraph boundaries
    # rather than fixed token count
    ...
    for chunk in semantic_chunks:
        chunk.metadata["guideline_version"] = version
        chunk.metadata["topic_id"] = topic_id
        chunk.metadata["last_updated"] = datetime.now().isoformat()
        chunk.metadata["source_type"] = "pdf_narrative"
```

### 5.2 Activate Hybrid BM25 + Vector Retrieval (Week 1–2)

> You already have `fix_bm25.py`. Wire it into `rag_engine.py` with Reciprocal Rank Fusion.

```python
# rag_engine.py — replace current single-retriever call

from langchain_community.retrievers import BM25Retriever
from langchain_chroma import Chroma
import pickle

def reciprocal_rank_fusion(
    bm25_results: list, 
    vector_results: list, 
    k: int = 60
) -> list:
    """
    Merge two ranked lists using RRF.
    k=60 is the standard constant from the original RRF paper.
    Returns deduplicated results sorted by combined score (descending).
    """
    scores: dict[str, float] = {}
    doc_map: dict[str, any] = {}
    
    for rank, doc in enumerate(bm25_results):
        doc_id = doc.page_content[:100]  # Use content prefix as stable key
        scores[doc_id] = scores.get(doc_id, 0) + 1 / (rank + k)
        doc_map[doc_id] = doc
    
    for rank, doc in enumerate(vector_results):
        doc_id = doc.page_content[:100]
        scores[doc_id] = scores.get(doc_id, 0) + 1 / (rank + k)
        doc_map[doc_id] = doc
    
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [doc_map[doc_id] for doc_id, _ in ranked]

def retrieve_hybrid(query: str, k: int = 10) -> list:
    """Run both retrievers and fuse results."""
    bm25_docs = bm25_retriever.get_relevant_documents(query)
    vector_docs = vector_retriever.get_relevant_documents(query)
    return reciprocal_rank_fusion(bm25_docs, vector_docs)[:k]
```

### 5.3 Add Abstention Gate (Week 2)

> This is the most important safety feature. Do not skip it.

```python
# rag_engine.py — add before RAG query execution

REQUIRED_CLINICAL_SIGNALS = [
    # At least one of these groups must be present in the FHIR bundle
    # for a recommendation to be generated
    "patient_age_present",
    "chief_complaint_present", 
    "body_region_identifiable",
]

ABSTENTION_THRESHOLD = 0.4  # Confidence below this → abstain

def evaluate_input_completeness(fhir_bundle: dict) -> tuple[bool, str]:
    """
    Check whether the clinical input has enough information to generate
    a safe recommendation. Returns (should_abstain, reason_text).
    
    Inspired by Tan et al. (2025) negative rejection validation.
    """
    missing = []
    
    patient = fhir_bundle.get("patient", {})
    conditions = fhir_bundle.get("conditions", [])
    observations = fhir_bundle.get("observations", [])
    
    # Check age
    if not patient.get("age") and not patient.get("birthDate"):
        missing.append("patient age")
    
    # Check clinical indication
    if not conditions:
        missing.append("clinical indication or diagnosis")
    
    # Check body region (parse from condition codes or text)
    body_regions = ["head", "chest", "abdomen", "pelvis", "spine", "extremity", 
                    "breast", "cardiac", "vascular", "brain", "lung", "liver",
                    "kidney", "hip", "knee", "shoulder", "ankle", "wrist"]
    condition_text = " ".join(
        c.get("display", "").lower() for c in conditions
    )
    if not any(region in condition_text for region in body_regions):
        missing.append("identifiable body region or organ system")
    
    if missing:
        reason = (
            f"Insufficient clinical information to generate a safe recommendation. "
            f"Missing: {', '.join(missing)}. "
            f"Please provide additional context."
        )
        return True, reason
    
    return False, ""

def query_acr_guidelines(clinical_text: str, fhir_bundle: dict = None) -> dict:
    """
    Main RAG query with abstention gate.
    Returns a structured result dict with either a recommendation or an abstention card.
    """
    # 1. Evaluate completeness
    if fhir_bundle:
        should_abstain, abstention_reason = evaluate_input_completeness(fhir_bundle)
        if should_abstain:
            return {
                "abstained": True,
                "recommendation": None,
                "abstention_reason": abstention_reason,
                "cds_card": build_clarification_card(abstention_reason),
                "sources": [],
            }
    
    # 2. Hybrid retrieval
    docs = retrieve_hybrid(clinical_text, k=10)
    
    # 3. Confidence scoring
    # (Simple heuristic: if top doc similarity score < threshold, abstain)
    # Replace with actual similarity scores from ChromaDB query
    
    # 4. Generation (existing Gemini call)
    recommendation = generate_recommendation(clinical_text, docs)
    
    return {
        "abstained": False,
        "recommendation": recommendation,
        "sources": [{"content": d.page_content, "metadata": d.metadata} for d in docs[:5]],
    }

def build_clarification_card(reason: str) -> dict:
    """
    Build a CDS Hooks card requesting additional clinical information.
    Routes to clarification workflow rather than generating a potentially unsafe recommendation.
    """
    return {
        "summary": "Additional Clinical Information Required",
        "indicator": "warning",
        "detail": reason,
        "source": {"label": "ACR-AC-RAG Safety Gate"},
        "suggestions": [
            {
                "label": "Provide complete clinical context",
                "actions": [{"type": "create", "description": "Return with complete patient age, indication, and body region"}]
            }
        ]
    }
```

### 5.4 Safety Engine → RAG Feedback Loop (Week 3)

> When Safety flags a hard contraindication, automatically re-query for alternatives.

```python
# main.py — add after safety_engine check

HARD_CONTRAINDICATION_TRIGGERS = [
    "contrast_allergy_severe",
    "egfr_critically_low",        # eGFR < 30
    "pregnancy_radiation_risk",
]

async def analyze_with_safety_loop(clinical_text: str, fhir_bundle: dict) -> dict:
    """
    Full pipeline with safety → RAG feedback loop.
    If safety engine flags a hard contraindication, re-query RAG for alternatives.
    """
    # Step 1: Initial RAG recommendation
    rag_result = query_acr_guidelines(clinical_text, fhir_bundle)
    
    if rag_result.get("abstained"):
        return rag_result
    
    # Step 2: Safety check
    safety_flags = run_safety_checks(fhir_bundle, rag_result.get("recommendation", ""))
    hard_flags = [f for f in safety_flags if f["severity"] in HARD_CONTRAINDICATION_TRIGGERS]
    
    # Step 3: If hard contraindication found, re-query for alternatives
    if hard_flags:
        contraindications = [f["description"] for f in hard_flags]
        alternative_query = (
            f"{clinical_text} "
            f"CONSTRAINT: The following are contraindicated: {', '.join(contraindications)}. "
            f"Recommend only non-contrast or alternative modality options."
        )
        alternative_result = query_acr_guidelines(alternative_query, fhir_bundle)
        
        return {
            **alternative_result,
            "safety_flags": safety_flags,
            "original_recommendation_contraindicated": True,
            "original_recommendation": rag_result.get("recommendation"),
            "contraindications": contraindications,
        }
    
    return {**rag_result, "safety_flags": safety_flags}
```

### 5.5 Guideline Version Tracking (Week 3)

```python
# ingest.py — add version metadata to all chunks

import hashlib
from datetime import datetime

def get_guideline_version(source_path: str) -> str:
    """Generate a content hash as version identifier."""
    with open(source_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()[:8]

# In ChromaDB metadata for every chunk:
metadata = {
    ...existing metadata...,
    "guideline_version": get_guideline_version(source_path),
    "ingested_at": datetime.now().isoformat(),
    "acr_publication_year": "2024",  # Parse from PDF or filename
}
```

**Add a version query to `rag_engine.py`** so CDS cards can surface guideline age:

```python
def get_source_version_info(docs: list) -> list[dict]:
    """Extract version metadata from retrieved docs for display in CDS card."""
    return [
        {
            "source": d.metadata.get("source", "Unknown"),
            "guideline_version": d.metadata.get("guideline_version", "Unknown"),
            "last_updated": d.metadata.get("last_updated", "Unknown"),
        }
        for d in docs
    ]
```

### 5.6 Override Capture Logging (Week 4 — Low effort, high long-term value)

```python
# main.py — add new endpoint

from datetime import datetime
import json

@app.post("/v1/override-log")
async def log_clinician_override(override: OverrideLogRequest):
    """
    Called when a clinician ignores the system recommendation and orders something different.
    This is the single most valuable training signal you will ever collect.
    Do not skip this endpoint.
    """
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "session_id": override.session_id,
        "scenario_text": override.scenario_text,
        "system_recommendation": override.system_recommendation,
        "clinician_ordered": override.clinician_ordered,
        "override_reason": override.override_reason,  # free text, optional
        "clinician_specialty": override.clinician_specialty,
        "institution_id": override.institution_id,
    }
    
    # Append to JSONL log file (easy to batch-process later for fine-tuning)
    with open("data/logs/override_log.jsonl", "a") as f:
        f.write(json.dumps(log_entry) + "\n")
    
    return {"status": "logged", "entry_id": override.session_id}

class OverrideLogRequest(BaseModel):
    session_id: str
    scenario_text: str
    system_recommendation: str
    clinician_ordered: str
    override_reason: str = ""
    clinician_specialty: str = ""
    institution_id: str = ""
```

### 5.7 Prompt Injection Hardening (Week 4)

```python
# copilot_engine.py — add input sanitization

import re

INJECTION_PATTERNS = [
    r"ignore (previous|all|prior|above) instructions",
    r"disregard (your|all|the) (system|previous|prior)",
    r"you are now",
    r"new instruction",
    r"forget (everything|all|your instructions)",
    r"act as (a |an )?(different|new|unrestricted)",
    r"<\s*(script|iframe|object|embed)",   # HTML injection
]

def sanitize_clinical_input(text: str) -> str:
    """
    Remove or flag potential prompt injection patterns from clinical text input.
    Clinical notes should never contain instruction-like language.
    """
    text_lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            raise ValueError(
                f"Input contains potentially unsafe content pattern: '{pattern}'. "
                f"Clinical scenario text should describe patient symptoms and history only."
            )
    
    # Truncate to reasonable clinical note length
    MAX_INPUT_CHARS = 4000
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
    
    # Structural separation: wrap patient data in explicit tags
    return f"[PATIENT_CLINICAL_DATA_START]\n{text}\n[PATIENT_CLINICAL_DATA_END]"

# In generate_copilot_response():
# Replace: redacted_prompt = redact_phi(prompt)
# With:
sanitized_input = sanitize_clinical_input(req.scenario_text)
redacted_prompt = redact_phi(prompt.replace(req.scenario_text, sanitized_input))
```

---

## 6. Phase 2 — Cognitive Safeguards & Hybrid Deployment (Months 4–6)

### 6.1 ColBERT Re-ranking Layer

> Adds token-level matching on top of your hybrid retrieval. Implement after hybrid RRF is stable and you have eval metrics to measure the delta.

```bash
pip install ragatouille
```

```python
# rag_engine.py — add ColBERT re-ranking step after RRF fusion

from ragatouille import RAGPretrainedModel

# Load once at startup
colbert = RAGPretrainedModel.from_pretrained("colbert-ir/colbertv2.0")

def rerank_with_colbert(query: str, docs: list, top_k: int = 5) -> list:
    """
    Re-rank RRF-fused results using ColBERT late-interaction scoring.
    Run this AFTER hybrid retrieval, not instead of it.
    """
    passages = [d.page_content for d in docs]
    results = colbert.rerank(query=query, documents=passages, k=top_k)
    
    # Map back to original Document objects
    reranked_docs = []
    for r in results:
        for doc in docs:
            if doc.page_content == r["content"]:
                reranked_docs.append(doc)
                break
    
    return reranked_docs
```

**Measure the delta against your eval set** before shipping. The Pambudi paper showed ~13% recall improvement — validate that you see similar gains on your clinical scenarios.

### 6.2 Mayo-Style Abstention Routing

> For genuinely ambiguous or out-of-distribution cases, route to human review rather than forcing a low-confidence recommendation.

```python
# main.py — add routing logic

AMBIGUITY_THRESHOLD = 0.55  # Cases below this confidence go to manual queue

@app.post("/v1/analyze")
async def analyze(request: AnalyzeRequest):
    result = await analyze_with_safety_loop(request.text, fhir_bundle)
    
    # Route to manual review queue if confidence is too low
    if result.get("confidence_score", 1.0) < AMBIGUITY_THRESHOLD:
        await route_to_manual_review_queue(request, result)
        return {
            **result,
            "cds_card": {
                "summary": "Complex Case — Senior Radiologist Review Requested",
                "indicator": "info",
                "detail": (
                    "This case has been flagged as potentially complex or ambiguous. "
                    "A senior radiologist has been notified and will review within [SLA_TIME]. "
                    "A manual protocol recommendation will follow."
                ),
                "source": {"label": "ACR-AC-RAG Abstention Router"},
            }
        }
    
    return result

async def route_to_manual_review_queue(request, result):
    """
    Log ambiguous case to a review queue (implement as DB table or message queue).
    """
    # Write to manual_review_queue table in SQLite
    # or publish to a message queue (Redis, RabbitMQ) for async processing
    ...
```

### 6.3 Pediatric Protocol Branching

```python
# fhir_converter.py — add age detection and pediatric flag

def detect_patient_age(fhir_bundle: dict) -> tuple[int | None, bool]:
    """Returns (age_years, is_pediatric)."""
    patient = fhir_bundle.get("patient", {})
    age = patient.get("age")
    
    if age is None and patient.get("birthDate"):
        from datetime import date
        birth = date.fromisoformat(patient["birthDate"])
        age = (date.today() - birth).days // 365
    
    is_pediatric = age is not None and age < 18
    return age, is_pediatric

# rag_engine.py — add pediatric filter to retrieval

def retrieve_hybrid(query: str, is_pediatric: bool = False, k: int = 10) -> list:
    """
    Pediatric cases use a weighted retrieval that prioritizes chunks with 
    non-empty Peds RRL metadata and pediatric-scoped guideline sections.
    """
    bm25_docs = bm25_retriever.get_relevant_documents(query)
    vector_docs = vector_retriever.get_relevant_documents(query)
    fused = reciprocal_rank_fusion(bm25_docs, vector_docs)
    
    if is_pediatric:
        # Boost chunks that have pediatric-specific data
        peds_boosted = sorted(
            fused,
            key=lambda d: (
                0 if d.metadata.get("peds_rrl", "") in ("", "N/A") else 1
            ),
            reverse=True
        )
        return peds_boosted[:k]
    
    return fused[:k]
```

### 6.4 On-Premises Docker Deployment

Create a local deployment mode that swaps Gemini for a local model:

```dockerfile
# Dockerfile.local — on-premises deployment without cloud LLM dependency

FROM python:3.11-slim

# Install Ollama for local model serving
RUN curl -fsSL https://ollama.ai/install.sh | sh

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application
COPY . .

# Default to local Ollama model; override with DEPLOYMENT_MODE=cloud for Gemini
ENV DEPLOYMENT_MODE=local
ENV LOCAL_MODEL=llama3.1:8b
ENV OLLAMA_HOST=http://localhost:11434

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```python
# llm_router.py — new file

import os

DEPLOYMENT_MODE = os.environ.get("DEPLOYMENT_MODE", "cloud")  # "cloud" | "local"

def get_llm_client():
    if DEPLOYMENT_MODE == "local":
        from langchain_community.llms import Ollama
        return Ollama(
            model=os.environ.get("LOCAL_MODEL", "llama3.1:8b"),
            base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        )
    else:
        # Existing Gemini client
        from google import genai
        return genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
```

---

## 7. Phase 3 — SMART on FHIR & EHR Embedding (Months 7–9)

### 7.1 SMART on FHIR App Shell

```html
<!-- smart_app/index.html — skeleton for EHR-embedded SMART app -->
<!DOCTYPE html>
<html>
<head>
  <script src="https://cdn.jsdelivr.net/npm/fhirclient/build/fhir-client.js"></script>
</head>
<body>
<script>
FHIR.oauth2.ready().then(client => {
  // Pull patient context from EHR
  return Promise.all([
    client.patient.read(),
    client.patient.request("Condition"),
    client.patient.request("Observation"),
    client.patient.request("AllergyIntolerance"),
    client.patient.request("MedicationStatement"),
  ]);
}).then(([patient, conditions, observations, allergies, medications]) => {
  // Package into your existing API format and call /v1/analyze
  const payload = {
    patient,
    conditions: conditions.entry?.map(e => e.resource) || [],
    observations: observations.entry?.map(e => e.resource) || [],
    allergies: allergies.entry?.map(e => e.resource) || [],
    medications: medications.entry?.map(e => e.resource) || [],
  };
  
  return fetch("https://your-api-endpoint/v1/analyze-fhir", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
}).then(r => r.json()).then(result => {
  // Render CDS card in the EHR sidebar
  renderCDSCard(result);
});
</script>
</body>
</html>
```

### 7.2 Self-Learning Protocol Mapping

> When `protocol_mapper.py` makes a high-confidence fuzzy match that gets accepted repeatedly without override, write it back to SQLite automatically.

```python
# protocol_mapper.py — add self-learning layer

CONFIDENCE_THRESHOLD_FOR_AUTO_WRITE = 0.85
MIN_ACCEPTANCE_COUNT = 3  # Must be accepted this many times before auto-write

def record_mapping_acceptance(
    acr_procedure: str, 
    local_protocol_id: int, 
    confidence: float,
    match_method: str  # "exact" | "fuzzy" | "llm"
):
    """Record that a mapping was accepted by a clinician without override."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO mapping_acceptance_log 
            (acr_procedure, local_protocol_id, confidence, match_method, accepted_at)
            VALUES (?, ?, ?, ?, datetime('now'))
        """, (acr_procedure, local_protocol_id, confidence, match_method))
        
        # Check if this mapping now qualifies for auto-write
        count = conn.execute("""
            SELECT COUNT(*) FROM mapping_acceptance_log 
            WHERE acr_procedure = ? AND local_protocol_id = ? AND confidence >= ?
        """, (acr_procedure, local_protocol_id, CONFIDENCE_THRESHOLD_FOR_AUTO_WRITE)).fetchone()[0]
        
        if count >= MIN_ACCEPTANCE_COUNT:
            # Auto-write to the primary mapping table
            existing = conn.execute("""
                SELECT id FROM acr_protocol_map WHERE acr_label = ?
            """, (acr_procedure,)).fetchone()
            
            if not existing:
                conn.execute("""
                    INSERT INTO acr_protocol_map (acr_label, protocol_id, auto_learned, learned_at)
                    VALUES (?, ?, 1, datetime('now'))
                """, (acr_procedure, local_protocol_id))
```

---

## 8. Phase 4 — RL, Governance & qCDSM Compliance (Months 10–12)

### 8.1 DSN Audit Log (qCDSM Compliance)

```python
# main.py — add DSN generation to every CDS hook transaction

import uuid
import hashlib
from datetime import datetime

def generate_dsn(session_data: dict) -> str:
    """
    Generate a tamper-evident Decision Support Number.
    Format: [SYSTEM_PREFIX]-[DATE]-[HASH]
    """
    content = f"{session_data['timestamp']}{session_data['patient_id']}{session_data['recommendation']}"
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:12].upper()
    date_str = datetime.now().strftime("%Y%m%d")
    return f"ACR-{date_str}-{content_hash}"

def log_dsn_transaction(session_data: dict, dsn: str):
    """
    Append DSN record to immutable audit log.
    Required for CMS qCDSM compliance.
    """
    record = {
        "dsn": dsn,
        "timestamp": session_data["timestamp"],
        "patient_mrn_hash": hashlib.sha256(
            session_data.get("patient_id", "").encode()
        ).hexdigest(),  # Store hashed MRN, never plain
        "recommendation_summary": session_data["recommendation_summary"],
        "guideline_version": session_data["guideline_version"],
        "appropriateness_category": session_data["appropriateness_category"],
        "api_version": "3.0.0",
        "g_code": "G1002",  # CMS G-code for advanced imaging CDS
    }
    
    with open("data/logs/dsn_audit_log.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
```

### 8.2 GRPO Fine-tuning Data Pipeline

> Once you have 6+ months of override logs, use them to fine-tune a local model.

```python
# scripts/prepare_grpo_training_data.py

"""
Converts override_log.jsonl into GRPO training pairs.
Each pair: (clinical_scenario, wrong_recommendation, correct_recommendation)
"""

import json

def build_training_pairs(override_log_path: str) -> list[dict]:
    pairs = []
    with open(override_log_path) as f:
        for line in f:
            entry = json.loads(line)
            # Only use cases where override_reason was provided (higher signal quality)
            if entry.get("override_reason"):
                pairs.append({
                    "prompt": entry["scenario_text"],
                    "chosen": entry["clinician_ordered"],      # Ground truth from radiologist
                    "rejected": entry["system_recommendation"], # What the model got wrong
                    "reason": entry["override_reason"],
                })
    return pairs
```

---

## 9. Architecture Diagrams

### Target Architecture (v3.0.0)

```
Clinical Text Input / FHIR Bundle / CDS Hook
              │
              ▼
┌─────────────────────────┐
│   Input Sanitization    │  ← Prompt injection guard
│   + PHI Redaction       │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   FHIR Converter        │  → Patient / Condition / Observation / AllergyIntolerance
│   + Age Detection       │  → Pediatric flag
│   + LOINC/SNOMED norm   │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   Abstention Gate       │  ← If sparse: return clarification CDS card, STOP
│   (Input Completeness)  │
└────────────┬────────────┘
             │ (sufficient input)
             ▼
┌─────────────────────────┐      ┌──────────────────┐
│   Hybrid Retrieval      │      │   BM25 Retriever │
│   BM25 + Vector (RRF)   │ ←────┤   (fix_bm25.py)  │
│   + ColBERT Re-ranking  │      └──────────────────┘
└────────────┬────────────┘      ┌──────────────────┐
             │          ←────────┤  ChromaDB Vector  │
             ▼                   └──────────────────┘
┌─────────────────────────┐
│   Safety Engine         │  → eGFR / Allergy / Pregnancy / Med Holds
│   + Contraindication    │
│     Re-query Loop       │  ← If hard flag: re-query with constraints
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   Protocol Mapper       │  → ACR recommendation → local scanner protocol
│   + Self-learning DB    │  → Auto-write high-confidence mappings
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   LLM Generation        │  → Gemini 2.5 Flash (cloud) OR Llama 3.1 8B (local)
│   (llm_router.py)       │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│   DSN Generator         │  → Tamper-evident audit log
│   + Override Logger     │  → Training data flywheel
└────────────┬────────────┘
             │
             ▼
    CDS Card Output (FastAPI)
    ├── /v1/cds-hook  (EHR integration)
    ├── /v1/analyze   (dashboard)
    └── SMART on FHIR (Epic/Cerner embedded)
```

---

## 10. File-by-File Refactor Notes

| File | Phase | Changes Required |
|---|---|---|
| `ingest.py` | 1 | Semantic + hierarchical chunking; guideline version metadata; pediatric chunk tagging |
| `rag_engine.py` | 1 | Activate hybrid BM25+vector RRF; abstention gate; confidence scoring; pediatric branch; ColBERT re-ranker (Phase 2) |
| `main.py` | 1 | Safety → RAG feedback loop; override log endpoint; DSN generation (Phase 4); abstention routing (Phase 2) |
| `copilot_engine.py` | 1 | Prompt injection sanitization; structural PHI separation |
| `fhir_converter.py` | 2 | Age detection; pediatric flag output |
| `protocol_mapper.py` | 3 | Self-learning acceptance log; auto-write to SQLite |
| `protocol_db.py` | 3 | Add `mapping_acceptance_log` table; `auto_learned` column on `acr_protocol_map` |
| `safety_engine.py` | 1 (minor) | Expose hard-flag list to main.py for re-query trigger |
| `fix_bm25.py` | 1 | Already correct; add to startup initialization in `rag_engine.py` |
| `app.py` | 3 | Replace with SMART on FHIR embedded app |
| `medical_ontology.py` | 2 | Add pediatric contrast weight-based dosing entries |
| `seed_db.py` | 3 | Add `mapping_acceptance_log` table to schema creation |
| New: `llm_router.py` | 2 | Cloud vs. local LLM routing |
| New: `metrics/eval.py` | Pre-work | MRR, Recall@K, appropriateness accuracy, abstention rate |
| New: `metrics/run_eval.py` | Pre-work | CLI runner for eval suite |

---

## 11. Data Schema Reference

### New Tables Required in SQLite

```sql
-- mapping_acceptance_log: tracks clinician acceptance of fuzzy-matched protocol mappings
CREATE TABLE IF NOT EXISTS mapping_acceptance_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    acr_procedure   TEXT NOT NULL,
    local_protocol_id INTEGER REFERENCES imaging_protocol(id),
    confidence      REAL NOT NULL,
    match_method    TEXT NOT NULL,  -- 'exact' | 'fuzzy' | 'llm'
    accepted_at     TEXT NOT NULL,
    institution_id  TEXT
);

-- manual_review_queue: ambiguous cases routed to senior radiologist
CREATE TABLE IF NOT EXISTS manual_review_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT UNIQUE NOT NULL,
    scenario_text   TEXT NOT NULL,
    confidence_score REAL,
    abstention_reason TEXT,
    status          TEXT DEFAULT 'pending',  -- 'pending' | 'reviewed' | 'resolved'
    reviewer_id     TEXT,
    reviewed_at     TEXT,
    final_recommendation TEXT,
    created_at      TEXT NOT NULL
);

-- dsn_audit_log: CMS qCDSM compliance records (also written to JSONL)
CREATE TABLE IF NOT EXISTS dsn_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dsn             TEXT UNIQUE NOT NULL,
    patient_mrn_hash TEXT NOT NULL,
    recommendation_summary TEXT,
    guideline_version TEXT,
    appropriateness_category TEXT,
    g_code          TEXT DEFAULT 'G1002',
    api_version     TEXT,
    created_at      TEXT NOT NULL
);
```

### ChromaDB Metadata Schema (All Chunks)

```json
{
  "topic_id": 42,
  "topic_name": "Headache",
  "scenario": "Thunderclap headache, first episode, no focal neurologic deficit",
  "procedure": "CT head without contrast",
  "appropriateness": "Usually Appropriate",
  "adult_rrl": "☢☢☢",
  "peds_rrl": "☢☢☢",
  "level": "procedure",
  "chunk_type": "structured_table",
  "source_type": "structured_table",
  "guideline_version": "a1b2c3d4",
  "ingested_at": "2026-05-26T10:00:00",
  "last_updated": "2026-05-26T10:00:00",
  "acr_publication_year": "2024",
  "is_pediatric": false
}
```

---

## 12. Key Dependencies & Versions

```txt
# Existing (keep)
fastapi==0.136.1
uvicorn==0.46.0
pydantic==2.13.4
langchain-core==1.4.0
langchain-chroma==1.1.0
langchain-google-genai==4.2.2
google-genai==1.75.0
python-dotenv==1.2.2
fhir.resources==8.2.0
requests==2.34.0
pypdf==6.11.0
chromadb==1.5.9

# Phase 1 additions
langchain-community>=0.2.0     # BM25Retriever already used in fix_bm25.py

# Phase 2 additions
ragatouille>=0.0.7             # ColBERT re-ranking
langchain-ollama>=0.1.0        # Local LLM via Ollama (on-premises deployment)

# Eval framework
pandas>=2.0.0                  # Ground truth CSV handling
scikit-learn>=1.3.0            # Metric calculations
```

---

## Appendix: Priority Order Summary

If you have limited bandwidth, execute in this exact order:

1. **Build eval set (75–100 scenarios)** — before touching any code
2. **Activate hybrid BM25 + vector retrieval** — already scaffolded, 1–2 days
3. **Add abstention gate** — highest clinical safety impact, ~1 week
4. **Fix ingest.py chunking** — foundational, do before anything else regresses
5. **Safety engine → RAG re-query loop** — unique competitive differentiator
6. **Override logging endpoint** — 1 day to build, months of value accumulation
7. **Prompt injection sanitization** — 1 day, security hygiene
8. **Guideline version metadata** — required for Phase 4 compliance work
9. **Pediatric branching** — Phase 2
10. **ColBERT re-ranking** — Phase 2, after hybrid RRF is stable and measured
11. **On-premises Docker** — Phase 2, unlocks new market segment
12. **SMART on FHIR app** — Phase 3, major frontend project
13. **Self-learning protocol mapping** — Phase 3
14. **DSN/qCDSM compliance** — Phase 4
15. **GRPO fine-tuning** — Phase 4, needs 6+ months of override log data first
