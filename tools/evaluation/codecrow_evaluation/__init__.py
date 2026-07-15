"""Offline, evidence-bound evaluation contracts for CodeCrow."""

from .scoring import EvaluationInputError, score_evaluation

__all__ = ["EvaluationInputError", "score_evaluation"]
__version__ = "1.0.0"
