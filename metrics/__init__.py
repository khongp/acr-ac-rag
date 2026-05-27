"""
ACR-AC-RAG Evaluation Metrics Package
======================================
Provides retrieval and classification metrics for evaluating the ACR
Appropriateness Criteria RAG pipeline.

Modules:
    eval        – Core metric functions (MRR, Recall@K, accuracy, rates).
    run_eval    – CLI runner for batch evaluation against ground-truth CSVs.
"""

from metrics.eval import (
    mean_reciprocal_rank,
    recall_at_k,
    appropriateness_accuracy,
    abstention_rate,
    override_rate,
)

__all__ = [
    "mean_reciprocal_rank",
    "recall_at_k",
    "appropriateness_accuracy",
    "abstention_rate",
    "override_rate",
]
