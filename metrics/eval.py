"""
ACR-AC-RAG Evaluation Metrics
==============================
Core metric functions for evaluating retrieval quality, classification
accuracy, and clinical-safety behaviour of the ACR Appropriateness
Criteria RAG pipeline.

Each function operates on *batch* inputs (lists of per-scenario results)
and returns a single scalar score in [0.0, 1.0].
"""

from __future__ import annotations


# ──────────────────────────────────────────────────────────────────────
# Retrieval metrics
# ──────────────────────────────────────────────────────────────────────

def _normalize_modality(name: str) -> str:
    """Helper to normalize modality names for robust comparison."""
    import re
    n = name.lower().strip()
    n = n.replace("iv contrast", "contrast")
    n = n.replace("without and with", "without/with")
    n = n.replace("without & with", "without/with")
    n = n.replace("without/with", "without with")
    n = n.replace("w/o & w/", "without with")
    n = n.replace("w/o and w/", "without with")
    n = n.replace("without contrast", "without")
    n = n.replace("with contrast", "with")
    n = n.replace("without and with contrast", "without with")
    n = n.replace("without & with contrast", "without with")
    n = n.replace("without/with contrast", "without with")
    n = n.replace("lower extremity", "legs")
    n = n.replace("lower extremities", "legs")
    n = n.replace("lower limb", "legs")
    n = n.replace("kidneys and bladder", "kidneys")
    n = n.replace("kidneys", "kidney")
    n = n.replace("ultrasound", "us")
    n = n.replace("radiography", "x-ray")
    n = n.replace("head", "brain")
    n = n.replace("skull", "brain")
    n = re.sub(r'[^a-z0-9]', '', n)
    return n

def _is_modality_match(pred: str, expected: str) -> bool:
    """Helper to check if two modality strings match semantically."""
    p_norm = _normalize_modality(pred)
    e_norm = _normalize_modality(expected)
    if not p_norm or not e_norm:
        return False
    # Direct match or substring match
    if p_norm == e_norm or p_norm in e_norm or e_norm in p_norm:
        return True
    # Strip common prefixes/suffixes and check again
    p_clean = p_norm.replace("us", "").replace("ct", "").replace("mri", "").replace("xray", "")
    e_clean = e_norm.replace("us", "").replace("ct", "").replace("mri", "").replace("xray", "")
    if p_clean and e_clean and (p_clean in e_clean or e_clean in p_clean):
        # Ensure we have some shared modality class
        p_mods = [m for m in ["us", "ct", "mri", "xray"] if m in p_norm]
        e_mods = [m for m in ["us", "ct", "mri", "xray"] if m in e_norm]
        if not p_mods or not e_mods or any(m in e_mods for m in p_mods):
            return True
    return False

def mean_reciprocal_rank(
    results: list[list[str]],
    ground_truth: list[str],
) -> float:
    """Compute Mean Reciprocal Rank (MRR) over a batch of queries using robust comparison."""
    if not results or not ground_truth:
        return 0.0

    if len(results) != len(ground_truth):
        raise ValueError(
            f"Length mismatch: results ({len(results)}) vs "
            f"ground_truth ({len(ground_truth)})"
        )

    rr_sum = 0.0
    for ranked_list, expected in zip(results, ground_truth):
        for rank, item in enumerate(ranked_list, start=1):
            if _is_modality_match(item, expected):
                rr_sum += 1.0 / rank
                break

    return rr_sum / len(results)


def recall_at_k(
    results: list[list[str]],
    ground_truth: list[str],
    k: int = 5,
) -> float:
    """Compute Recall@K over a batch of queries using robust comparison."""
    if not results or not ground_truth:
        return 0.0

    if len(results) != len(ground_truth):
        raise ValueError(
            f"Length mismatch: results ({len(results)}) vs "
            f"ground_truth ({len(ground_truth)})"
        )

    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")

    hits = 0
    for ranked_list, expected in zip(results, ground_truth):
        top_k = ranked_list[:k]
        if any(_is_modality_match(item, expected) for item in top_k):
            hits += 1

    return hits / len(results)


# ──────────────────────────────────────────────────────────────────────
# Classification metrics
# ──────────────────────────────────────────────────────────────────────

def appropriateness_accuracy(
    predictions: list[str],
    ground_truth: list[str],
) -> float:
    """Compute exact-match accuracy for appropriateness categories.

    Comparison is **case-insensitive** and strips surrounding whitespace.

    Parameters
    ----------
    predictions : list[str]
        Predicted appropriateness labels, e.g.
        ``["Usually Appropriate", "May Be Appropriate", ...]``.
    ground_truth : list[str]
        Expected appropriateness labels.

    Returns
    -------
    float
        Fraction of exact matches in [0.0, 1.0].

    Examples
    --------
    >>> appropriateness_accuracy(
    ...     ["Usually Appropriate", "May Be Appropriate"],
    ...     ["Usually Appropriate", "Usually Not Appropriate"],
    ... )
    0.5
    """
    if not predictions or not ground_truth:
        return 0.0

    if len(predictions) != len(ground_truth):
        raise ValueError(
            f"Length mismatch: predictions ({len(predictions)}) vs "
            f"ground_truth ({len(ground_truth)})"
        )

    matches = sum(
        1
        for pred, expected in zip(predictions, ground_truth)
        if pred.strip().lower() == expected.strip().lower()
    )
    return matches / len(predictions)


# ──────────────────────────────────────────────────────────────────────
# Clinical-safety metrics
# ──────────────────────────────────────────────────────────────────────

def abstention_rate(predictions: list[dict]) -> float:
    """Compute the abstention rate across predictions.

    A prediction is counted as an *abstention* when the system correctly
    withheld a recommendation.  The function looks for a boolean key
    ``"abstained"`` in each prediction dict.

    Parameters
    ----------
    predictions : list[dict]
        Each dict should contain at minimum::

            {
                "recommendation": str,   # The RAG-generated text
                "abstained": bool,       # True if system withheld answer
            }

        If the ``"abstained"`` key is missing the prediction is treated
        as *not* abstained.

    Returns
    -------
    float
        Fraction of predictions where the system abstained, in
        [0.0, 1.0].

    Examples
    --------
    >>> abstention_rate([
    ...     {"recommendation": "...", "abstained": True},
    ...     {"recommendation": "...", "abstained": False},
    ...     {"recommendation": "...", "abstained": True},
    ... ])
    0.6666666666666666
    """
    if not predictions:
        return 0.0

    abstentions = sum(
        1 for p in predictions if p.get("abstained", False)
    )
    return abstentions / len(predictions)


def override_rate(predictions: list[dict]) -> float:
    """Compute the clinician override rate across predictions.

    An *override* occurs when a clinician changed the system's
    recommendation.  The function looks for a boolean key
    ``"overridden"`` in each prediction dict.

    Parameters
    ----------
    predictions : list[dict]
        Each dict should contain at minimum::

            {
                "recommendation": str,
                "overridden": bool,       # True if clinician overrode
                "override_reason": str,   # Optional reason text
            }

        If the ``"overridden"`` key is missing the prediction is treated
        as *not* overridden.

    Returns
    -------
    float
        Fraction of predictions where a clinician override occurred, in
        [0.0, 1.0].

    Examples
    --------
    >>> override_rate([
    ...     {"recommendation": "...", "overridden": True,
    ...      "override_reason": "Patient allergy to contrast"},
    ...     {"recommendation": "...", "overridden": False},
    ... ])
    0.5
    """
    if not predictions:
        return 0.0

    overrides = sum(
        1 for p in predictions if p.get("overridden", False)
    )
    return overrides / len(predictions)
