#!/usr/bin/env python3
"""
ACR-AC-RAG Evaluation Runner
==============================
CLI tool that evaluates the RAG pipeline against a ground-truth CSV of
clinical scenarios and writes a JSON report with retrieval / classification
metrics.

Usage
-----
::

    # Full evaluation run
    python -m metrics.run_eval \
        --test-set data/eval/ground_truth.csv \
        --report   data/eval/report.json

    # Dry-run (validate CSV only)
    python -m metrics.run_eval \
        --test-set data/eval/ground_truth.csv \
        --dry-run

    # With minimum accuracy gate (CI / CD)
    python -m metrics.run_eval \
        --test-set data/eval/ground_truth.csv \
        --report   data/eval/report.json \
        --min-accuracy 0.70
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Ensure the project root is importable when running from the repo root
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from metrics.eval import (
    mean_reciprocal_rank,
    recall_at_k,
    appropriateness_accuracy,
    abstention_rate,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUIRED_COLUMNS = {
    "scenario_text",
    "expected_modality",
    "expected_appropriateness",
    "expected_variant_id",
    "notes",
}

VALID_APPROPRIATENESS = {
    "usually appropriate",
    "may be appropriate",
    "usually not appropriate",
}

# Phrases the LLM emits when it cannot match any ACR guideline
_ABSTENTION_PHRASES = [
    "i cannot answer",
    "no relevant information",
    "unable to provide a recommendation",
    "no matching acr",
    "outside the scope",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_ground_truth(csv_path: str) -> list[dict]:
    """Load and validate the ground-truth CSV.

    Parameters
    ----------
    csv_path : str
        Path to a CSV file with columns defined in ``REQUIRED_COLUMNS``.

    Returns
    -------
    list[dict]
        One dict per scenario row.

    Raises
    ------
    FileNotFoundError
        If the CSV does not exist.
    ValueError
        If required columns are missing or the file is empty.
    """
    csv_path = os.path.abspath(csv_path)
    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"Ground-truth CSV not found: {csv_path}")

    rows: list[dict] = []
    with open(csv_path, "r", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)

        # Validate header
        if reader.fieldnames is None:
            raise ValueError("CSV appears to be empty (no header row).")
        header_set = {c.strip().lower() for c in reader.fieldnames}
        missing = REQUIRED_COLUMNS - header_set
        if missing:
            raise ValueError(
                f"CSV is missing required columns: {sorted(missing)}"
            )

        for idx, row in enumerate(reader, start=2):  # row 1 = header
            # Normalise keys to lowercase/stripped
            normalised = {k.strip().lower(): v.strip() for k, v in row.items()}
            if not normalised.get("scenario_text"):
                print(f"[WARN] Row {idx}: empty scenario_text — skipping.")
                continue

            appropriateness = normalised.get("expected_appropriateness", "")
            if appropriateness.lower() not in VALID_APPROPRIATENESS:
                print(
                    f"[WARN] Row {idx}: unexpected appropriateness value "
                    f"'{appropriateness}'. Keeping as-is."
                )
            rows.append(normalised)

    if not rows:
        raise ValueError("Ground-truth CSV contains no data rows.")

    return rows


def extract_modality_from_response(response_text: str) -> Optional[str]:
    """Best-effort extraction of the recommended imaging modality from RAG output.

    Cleans markdown formatting, handles multi-line lists, and extracts the first
    recommended modality under the 'Usually Appropriate' section.
    """
    if not response_text:
        return None

    # Clean markdown formatting first
    clean_text = response_text.replace("**", "").replace("*", "").replace("_", "")
    lines = [line.strip() for line in clean_text.split("\n") if line.strip()]

    # Find the usually appropriate section
    for idx, line in enumerate(lines):
        line_lower = line.lower()
        if "usually appropriate" in line_lower or "rating 7-9" in line_lower or "usually appropriate:" in line_lower:
            # Look at lines below
            for next_line in lines[idx+1:]:
                # If we encounter another header class, stop
                next_lower = next_line.lower()
                if "may be appropriate" in next_lower or "usually not appropriate" in next_lower or "rating 4-6" in next_lower or "rating 1-3" in next_lower or next_line.startswith("- Plan") or next_line.startswith("- Estimate") or next_line.startswith("- Final"):
                    break
                # Strip leading bullets/numbers/whitespace
                clean_line = re.sub(r'^[•\-\*\s\d\.\(\)]+', '', next_line).strip()
                if clean_line and len(clean_line) > 2:
                    return clean_line
            # Check if there was something inline after the colon/dash on this line
            parts = re.split(r'[:\-–—]', line, maxsplit=1)
            if len(parts) > 1:
                inline = re.sub(r'^[•\-\*\s\d\.\(\)]+', '', parts[1]).strip()
                if inline and len(inline) > 2:
                    return inline

    # Fallback to general recommend pattern
    for line in lines:
        line_lower = line.lower()
        if "recommend" in line_lower or "imaging:" in line_lower:
            parts = re.split(r'[:\-–—]', line, maxsplit=1)
            if len(parts) > 1:
                clean_line = re.sub(r'^[•\-\*\s\d\.\(\)]+', '', parts[1]).strip()
                if clean_line and len(clean_line) > 2:
                    return clean_line

    # Last resort: scan for known modality keywords
    known_modalities = [
        "CT angiography", "CTA", "CT with contrast", "CT without contrast",
        "CT head", "CT abdomen", "CT chest", "CT",
        "MRI with contrast", "MRI without contrast", "MRI brain", "MRI",
        "MRA", "X-ray", "Radiography", "US", "Ultrasound",
        "PET/CT", "PET-CT", "FDG-PET", "Nuclear medicine",
        "Fluoroscopy", "Echocardiography", "MRCP",
    ]
    text_lower = clean_text.lower()
    for mod in known_modalities:
        if mod.lower() in text_lower:
            return mod

    return None


def is_abstention(response_text: str) -> bool:
    """Return True if the response indicates the system abstained."""
    if not response_text:
        return True
    text_lower = response_text.lower()
    return any(phrase in text_lower for phrase in _ABSTENTION_PHRASES)


def _print_summary_table(report: dict) -> None:
    """Pretty-print a summary table to stdout."""
    metrics = report.get("metrics", {})
    meta = report.get("metadata", {})

    sep = "+" + "-" * 36 + "+" + "-" * 14 + "+"
    header = f"| {'Metric':<34} | {'Value':>12} |"

    print("\n" + "=" * 54)
    print("  ACR-AC-RAG Evaluation Report")
    print("=" * 54)
    print(sep)
    print(header)
    print(sep)

    def _row(label: str, value) -> str:
        if isinstance(value, float):
            return f"| {label:<34} | {value:>11.4f} |"
        return f"| {label:<34} | {str(value):>12} |"

    print(_row("Total scenarios", meta.get("total_scenarios", "N/A")))
    print(_row("Evaluated scenarios", meta.get("evaluated_scenarios", "N/A")))
    print(_row("Errors", meta.get("errors", "N/A")))
    print(sep)
    print(_row("MRR", metrics.get("mrr", 0.0)))
    print(_row("Recall@3", metrics.get("recall_at_3", 0.0)))
    print(_row("Recall@5", metrics.get("recall_at_5", 0.0)))
    print(_row("Recall@10", metrics.get("recall_at_10", 0.0)))
    print(_row("Appropriateness Accuracy", metrics.get("appropriateness_accuracy", 0.0)))
    print(_row("Abstention Rate", metrics.get("abstention_rate", 0.0)))
    print(sep)

    elapsed = meta.get("elapsed_seconds")
    if elapsed is not None:
        print(f"  Elapsed: {elapsed:.1f}s")
    print()


# ---------------------------------------------------------------------------
# Main evaluation loop
# ---------------------------------------------------------------------------

def run_evaluation(
    csv_path: str,
    report_path: str | None = None,
    dry_run: bool = False,
    min_accuracy: float | None = None,
) -> dict:
    """Execute a full evaluation pass.

    Parameters
    ----------
    csv_path : str
        Path to the ground-truth CSV.
    report_path : str or None
        If provided, the JSON report is written here.
    dry_run : bool
        If True, only validate the CSV without invoking the RAG engine.
    min_accuracy : float or None
        If set, the function returns exit-code 1 when appropriateness
        accuracy is below this threshold.

    Returns
    -------
    dict
        The evaluation report.
    """
    # ── Step 1: Load & validate CSV ────────────────────────────────────
    scenarios = load_ground_truth(csv_path)
    print(f"[EVAL] Loaded {len(scenarios)} scenarios from {csv_path}")

    if dry_run:
        print("[DRY-RUN] CSV validated successfully. No RAG queries executed.")
        report = {
            "metadata": {
                "csv_path": csv_path,
                "total_scenarios": len(scenarios),
                "dry_run": True,
            },
            "metrics": {},
            "per_scenario": [],
        }
        if report_path:
            os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=2)
            print(f"[EVAL] Dry-run report written to {report_path}")
        return report

    # ── Step 2: Initialise RAG engine ──────────────────────────────────
    from rag_engine import init_rag, query_acr_guidelines  # noqa: E402

    print("[EVAL] Initialising RAG engine …")
    init_rag()

    # ── Step 3: Run each scenario ──────────────────────────────────────
    all_retrieved_modalities: list[list[str]] = []
    all_expected_modalities: list[str] = []
    all_predicted_appropriateness: list[str] = []
    all_expected_appropriateness: list[str] = []
    prediction_dicts: list[dict] = []
    per_scenario_results: list[dict] = []
    errors = 0
    t0 = time.time()

    for idx, row in enumerate(scenarios, start=1):
        scenario_text = row["scenario_text"]
        expected_mod = row["expected_modality"]
        expected_app = row["expected_appropriateness"]
        variant_id = row.get("expected_variant_id", "")
        notes = row.get("notes", "")

        print(f"  [{idx}/{len(scenarios)}] {scenario_text[:80]}…")

        try:
            result = query_acr_guidelines(scenario_text)
            rec_text = result.get("recommendation", "")

            # Extract modality from response
            predicted_mod = extract_modality_from_response(rec_text)
            abstained = is_abstention(rec_text)

            # Build ordered list of modalities mentioned for retrieval metrics
            retrieved_modalities = []
            if predicted_mod:
                retrieved_modalities.append(predicted_mod)
            # Also pull modalities from sources
            for src in result.get("sources", []):
                content = src.get("content", "")
                m = re.search(r"Procedure:\s*(.+?)(?:\n|$)", content)
                if m:
                    proc = m.group(1).strip()
                    if proc and proc not in retrieved_modalities:
                        retrieved_modalities.append(proc)

            # Determine predicted appropriateness from sources
            predicted_app = ""
            for src in result.get("sources", []):
                content = src.get("content", "")
                m = re.search(
                    r"Appropriateness Category:\s*(.+?)(?:\n|$)", content
                )
                if m:
                    predicted_app = m.group(1).strip()
                    break
            if not predicted_app:
                # Fall back to LLM text
                for label in [
                    "Usually Appropriate",
                    "May Be Appropriate",
                    "Usually Not Appropriate",
                ]:
                    if label.lower() in rec_text.lower():
                        predicted_app = label
                        break

            all_retrieved_modalities.append(retrieved_modalities)
            all_expected_modalities.append(expected_mod)
            all_predicted_appropriateness.append(predicted_app)
            all_expected_appropriateness.append(expected_app)
            prediction_dicts.append({
                "recommendation": rec_text,
                "abstained": abstained,
                "overridden": False,  # No override data during eval
            })

            per_scenario_results.append({
                "scenario_text": scenario_text,
                "expected_modality": expected_mod,
                "predicted_modality": predicted_mod,
                "expected_appropriateness": expected_app,
                "predicted_appropriateness": predicted_app,
                "expected_variant_id": variant_id,
                "abstained": abstained,
                "retrieved_modalities": retrieved_modalities[:10],
                "notes": notes,
            })

        except Exception as exc:
            errors += 1
            print(f"    [ERROR] {exc}")
            per_scenario_results.append({
                "scenario_text": scenario_text,
                "error": str(exc),
            })
            # Pad metric lists so indices stay aligned
            all_retrieved_modalities.append([])
            all_expected_modalities.append(expected_mod)
            all_predicted_appropriateness.append("")
            all_expected_appropriateness.append(expected_app)
            prediction_dicts.append({
                "recommendation": "",
                "abstained": True,
                "overridden": False,
            })

    elapsed = time.time() - t0

    # ── Step 4: Compute metrics ────────────────────────────────────────
    mrr = mean_reciprocal_rank(all_retrieved_modalities, all_expected_modalities)
    r_at_3 = recall_at_k(all_retrieved_modalities, all_expected_modalities, k=3)
    r_at_5 = recall_at_k(all_retrieved_modalities, all_expected_modalities, k=5)
    r_at_10 = recall_at_k(all_retrieved_modalities, all_expected_modalities, k=10)
    app_acc = appropriateness_accuracy(
        all_predicted_appropriateness, all_expected_appropriateness
    )
    abst_rate = abstention_rate(prediction_dicts)

    report = {
        "metadata": {
            "csv_path": csv_path,
            "total_scenarios": len(scenarios),
            "evaluated_scenarios": len(scenarios) - errors,
            "errors": errors,
            "elapsed_seconds": round(elapsed, 2),
            "dry_run": False,
        },
        "metrics": {
            "mrr": round(mrr, 4),
            "recall_at_3": round(r_at_3, 4),
            "recall_at_5": round(r_at_5, 4),
            "recall_at_10": round(r_at_10, 4),
            "appropriateness_accuracy": round(app_acc, 4),
            "abstention_rate": round(abst_rate, 4),
        },
        "per_scenario": per_scenario_results,
    }

    # ── Step 5: Write report ───────────────────────────────────────────
    if report_path:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        print(f"[EVAL] Report written to {report_path}")

    _print_summary_table(report)

    # ── Step 6: Accuracy gate ──────────────────────────────────────────
    if min_accuracy is not None and app_acc < min_accuracy:
        print(
            f"[FAIL] Appropriateness accuracy {app_acc:.4f} is below "
            f"threshold {min_accuracy:.4f}."
        )
        sys.exit(1)

    return report


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the ACR-AC-RAG pipeline against a ground-truth CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--test-set",
        required=True,
        help="Path to the ground-truth CSV file.",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path to write the JSON evaluation report.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the CSV format without running the RAG pipeline.",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=None,
        help=(
            "Minimum appropriateness accuracy threshold (0.0–1.0). "
            "Exits with code 1 if the actual accuracy falls below this value."
        ),
    )

    args = parser.parse_args()
    run_evaluation(
        csv_path=args.test_set,
        report_path=args.report,
        dry_run=args.dry_run,
        min_accuracy=args.min_accuracy,
    )


if __name__ == "__main__":
    main()
