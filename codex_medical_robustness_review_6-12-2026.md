# Codex Medical Robustness Review - 6/12/2026

This review adds findings beyond `6-11-2026_codebase_review.md`, `gemini_thoughts_6-11-2026_codebase-review.md`, and `codex_addendum_6-12-2026_codebase-review.md`.

Scope: static review only. The app and tests were not executed.

---

## Executive Summary

The prior reviews covered the major security issues well: exposed API key, unauthenticated write endpoints, unsafe `innerHTML`, unsafe pickle loading, error leakage, and SQLite/GCS persistence risks.

This pass focused on additional bug, security, optimization, and medical robustness risks. The most important theme is clinical correctness: the app should distinguish “safe,” “not evaluated,” “insufficient data,” and “synthetic/demo data,” and should avoid converting uncertain clinical facts into authoritative safety-clearance signals.

Highest-priority additions:

1. Free-text lab values are treated as fresh results.
2. Pregnancy observations using FHIR `valueCodeableConcept` are not parsed.
3. Medication holds do not affect top-level safety status.
4. Missing hard-stop labs do not trigger alternative routing/re-query.
5. Frontend `?api=` override can persist an attacker-controlled API endpoint.
6. Protocol mapping can conflate the proposed order with the recommended order.

---

## High-Priority Findings

### H1. Free-Text Labs Are Treated as Fresh Results

**Files:**

- `fhir_converter.py`
- `safety_engine.py`

**Issue:**

Regex and LLM-extracted labs are assigned `effectiveDateTime = date.today()` when converted into synthetic FHIR observations. The safety engine later uses that date for lab staleness checks.

Examples:

- `eGFR 28 last year`
- `creatinine elevated last month`
- `INR 1.2` with no date

These can become same-day observations, causing stale or undated labs to look current.

**Risk:**

This can falsely clear contrast or IR safety checks. In a clinical workflow, an undated lab should not be treated as a verified current result.

**Recommended fix:**

- Preserve extracted lab dates when explicitly present.
- Mark undated free-text labs as `date_confidence = "unknown"` or `source = "free_text_undated"`.
- Treat undated safety-critical labs as “requires verification.”
- In the safety panel, display “Lab value mentioned but date not verified” instead of “passed.”

---

### H2. Pregnancy Observations Using `valueCodeableConcept` Are Not Parsed

**Files:**

- `index.html`
- `safety_engine.py`

**Issue:**

The frontend simulator sends pregnancy status as FHIR `valueCodeableConcept`, but `_extract_observation()` only reads:

- `valueQuantity`
- `valueString`

It does not read:

- `valueCodeableConcept.text`
- `valueCodeableConcept.coding[].display`
- `valueCodeableConcept.coding[].code`

**Risk:**

Pregnancy values from the simulator or real EHR FHIR feeds may be ignored. This can cause fetal radiation or contrast checks to behave incorrectly.

**Recommended fix:**

Extend observation extraction to parse `valueCodeableConcept`.

Expected behavior:

- Positive pregnancy test -> warning/hard stop depending protocol.
- Pending/indeterminate pregnancy test -> verification required.
- Negative pregnancy test -> pass only if date is recent enough.

---

### H3. Medication Holds Do Not Affect `overall_status`

**File:**

- `safety_engine.py`

**Issue:**

`SafetyProfile.compute_overall_status()` considers:

- Triggered safety flags.
- Failed lab checks.

It does not consider medication hold alerts, even when `patient_is_taking = true`.

**Risk:**

The UI can show a medication hold card while the top-level safety summary still reports “clear.” That is clinically misleading.

**Recommended fix:**

Update `compute_overall_status()` so active medication holds make the profile at least `warnings`.

If a medication hold has bridging requirements, high bleeding risk, or procedure-specific hard-stop behavior, escalate accordingly.

---

### H4. Missing Hard-Stop Labs Do Not Trigger Alternative Routing

**File:**

- `safety_engine.py`

**Issue:**

`get_hard_contraindication_triggers()` only adds hard-stop lab triggers when `patient_value is not None`.

For IR workflows, missing required labs such as INR or platelets may be a hard stop or at minimum an insufficient-data stop.

**Risk:**

Cases with missing required labs may not trigger hard-stop handling or alternative re-query behavior.

**Recommended fix:**

- Treat missing required hard-stop labs as a blocking safety condition.
- Distinguish:
  - `abnormal_value`
  - `missing_required_lab`
  - `stale_required_lab`
- Route each condition to the appropriate UI and CDS Hooks response.

---

### H5. Frontend `?api=` Override Can Persist an Attacker-Controlled Endpoint

**File:**

- `index.html`

**Issue:**

The frontend supports `?api=https://...` and stores the value in `localStorage` as `API_BASE`.

**Risk:**

A crafted link could make a user's browser persist an attacker-controlled API endpoint. Future clinical scenarios and FHIR bundles could then be sent to that endpoint.

**Recommended fix:**

- Disable `?api=` override outside local development.
- Or restrict it to an explicit allowlist of trusted API origins.
- Clear untrusted saved API bases on load.
- Consider showing the active API origin visibly in development mode only.

---

### H6. Proposed Order and Recommended Order Can Be Conflated

**File:**

- `protocol_mapper.py`

**Issue:**

`get_draft_protocol()` prioritizes a `ServiceRequest` procedure from the FHIR bundle and overwrites the ACR-derived procedure for mapping and safety evaluation.

This is useful for checking the safety of the proposed order, but it can blur two distinct concepts:

- What the clinician/EHR proposed.
- What ACR recommends.

**Risk:**

The app may map and display a draft protocol for the proposed order while users interpret it as the recommended ACR order.

**Recommended fix:**

Represent these separately:

- `ordered_procedure`
- `recommended_procedure`
- `mapped_protocol_for_ordered_procedure`
- `mapped_protocol_for_recommended_procedure`

In the UI/CDS response, explicitly label whether the draft protocol is for the proposed order or for the recommended alternative.

---

## Medium-Priority Findings

### M1. “Clear” Safety State Is Overloaded

**Files:**

- `safety_engine.py`
- `index.html`

**Issue:**

`SafetyProfile.overall_status` defaults to `clear`. The UI also presents “patient clear” when no triggered rules are found.

But there are clinically different states:

- Rules evaluated and passed.
- Rules not evaluated.
- No matching protocol.
- Missing patient data.
- Synthetic/demo data only.
- FHIR live data available.

**Risk:**

Users may read “clear” as a stronger safety statement than the system can support.

**Recommended fix:**

Add explicit statuses:

- `clear`
- `warnings`
- `hard_stop`
- `insufficient_data`
- `not_evaluated`

Also expose `data_source` prominently in the UI:

- `synthetic`
- `manual_override`
- `fhir_live`
- `free_text_extracted`

---

### M2. Audit Tokens Are Not Actually Tamper-Evident

**File:**

- `main.py`

**Issue:**

Audit tokens are generated from an unsalted truncated SHA-256 hash of timestamp, scenario, and recommendation.

**Risk:**

Anyone who can edit the audit JSONL can recompute matching tokens. This is not truly tamper-evident.

**Recommended fix:**

- Use HMAC-SHA256 with a secret key.
- Consider a hash chain where each audit record includes the previous record hash.
- Store durable audit logs in append-only/cloud logging infrastructure rather than mutable local/GCS files.

---

### M3. Local Simulation FHIR Is Not Fully Representative

**File:**

- `index.html`

**Issue:**

The frontend constructs synthetic FHIR bundles independently from the backend. These bundles differ from backend-generated FHIR and from likely EHR FHIR in details such as value types, coding, subjects, and date handling.

**Risk:**

Demo safety behavior may diverge from production safety behavior.

**Recommended fix:**

- Move simulator bundle generation to a backend helper endpoint.
- Reuse the same FHIR construction/parsing pathway for demo and production-like flows.
- Add regression tests using realistic FHIR R4 examples.

---

### M4. CDS Hooks “No Cards” Message Overstates Safety

**File:**

- `index.html`

**Issue:**

When CDS Hooks returns no cards, the UI says the order proposal matches guidelines and has no contraindications.

**Risk:**

No cards can also mean:

- Safety was not evaluated.
- Protocol mapping failed.
- Required data was missing.
- RAG retrieval failed silently upstream.

**Recommended fix:**

Use more conservative text:

> No CDS cards were returned. Confirm that guideline retrieval, protocol mapping, and safety checks completed before treating this order as cleared.

Better still, return explicit status metadata from the CDS endpoint.

---

### M5. CI Coverage Is Too Narrow for a Medical App

**File:**

- `.github/workflows/deploy.yml`

**Issue:**

The deploy workflow runs compile checks and `test_safety_security.py`, but does not appear to run the broader test suite or frontend/security checks.

**Risk:**

Clinical edge-case regressions can reach deployment.

**Recommended fix:**

Add CI coverage for:

- FHIR parsing edge cases.
- Pregnancy values using `valueCodeableConcept`.
- Stale, missing, and undated labs.
- Medication holds affecting overall status.
- Protocol mapping distinction between proposed and recommended procedures.
- Confidence routing/manual review.
- Stored XSS rendering safety.
- Secret scanning.
- Dependency vulnerability scanning.

---

## Optimization Opportunities

### O1. Avoid Duplicate FHIR Bundle Construction Paths

The frontend constructs FHIR bundles for normal protocol analysis and CDS simulation separately. This duplicates logic and increases drift.

Recommended fix:

- Centralize simulator FHIR creation.
- Use backend-generated synthetic FHIR for both main analysis and CDS Hook simulation.

---

### O2. Add Clinical Provenance Metadata

Each extracted safety datum should carry provenance:

- Source: `fhir_live`, `free_text`, `manual_simulator`, `llm_extracted`, `regex_extracted`.
- Date source: explicit, inferred, missing.
- Confidence: high, medium, low.

This improves both safety behavior and UI transparency.

---

### O3. Cache and Display Guideline/Data Versions

The app exposes a guideline manifest, but clinical outputs should also include guideline version/build metadata in every response.

Recommended fix:

- Add `guideline_version`.
- Add `protocol_library_version`.
- Add `safety_rules_version`.
- Include these in audit logs.

---

## Recommended Implementation Order

### Phase 1 - Clinical Safety Correctness

1. Preserve lab date provenance and treat undated labs as requiring verification.
2. Parse FHIR `valueCodeableConcept` for pregnancy and other observations.
3. Make medication holds affect `overall_status`.
4. Treat missing/stale hard-stop labs as blocking or insufficient-data states.
5. Add explicit `not_evaluated` and `insufficient_data` safety states.

### Phase 2 - Data Flow and UI Clarity

1. Separate proposed order from recommended order in protocol mapping.
2. Make the UI label whether a draft protocol maps the proposed order or the recommended alternative.
3. Replace overconfident “clear”/“no cards” messages with status-aware language.
4. Display safety data provenance and source type.

### Phase 3 - Security and Operational Hardening

1. Disable or allowlist frontend `?api=` override outside local development.
2. Convert audit tokens to HMAC or hash-chain records.
3. Add body size limits and broader endpoint auth/rate limiting as described in prior reviews.
4. Expand CI with clinical edge-case tests, secret scanning, and dependency scanning.

### Phase 4 - Maintainability and Performance

1. Centralize synthetic FHIR bundle construction.
2. Add version metadata to every recommendation and audit record.
3. Reduce duplicate frontend/backend clinical transformation logic.

---

## Bottom Line

The next wave of work should make the app more medically robust, not just more secure.

The most important principle is:

> Do not convert uncertain, stale, missing, synthetic, or free-text-derived clinical data into a confident safety clearance.

The app should be conservative by default: when clinical data is incomplete or provenance is weak, surface that clearly and require verification.
