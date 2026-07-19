"""Offline, evidence-bound evaluation contracts for CodeCrow."""

from .scoring import EvaluationInputError, score_evaluation
from .comparison import compare_approaches

__all__ = ["EvaluationInputError", "compare_approaches", "score_evaluation"]
__version__ = "1.0.0"
