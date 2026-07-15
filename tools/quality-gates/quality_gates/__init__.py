"""Fail-closed quality gates for the LLM handoff program."""

from .changed_coverage import GateInputError, GateResult, evaluate_gate

__all__ = ["GateInputError", "GateResult", "evaluate_gate"]

