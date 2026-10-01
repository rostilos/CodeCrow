"""
Unit tests for service.review.orchestrator.stage_helpers.
"""
import json
import pytest
from service.review.orchestrator.stage_helpers import (
    emit_status,
    emit_progress,
    emit_error,
    format_project_rules,
    format_project_rules_digest,
)


# ── emit helpers ─────────────────────────────────────────────

class TestEmitHelpers:
    def test_emit_status_with_callback(self):
        events = []
        emit_status(events.append, "started", "hello")
        assert events[0]["type"] == "status"
        assert events[0]["state"] == "started"

    def test_emit_status_no_callback(self):
        emit_status(None, "started", "hello")  # no-op, no raise

    def test_emit_progress(self):
        events = []
        emit_progress(events.append, 50, "halfway")
        assert events[0]["percent"] == 50

    def test_emit_error(self):
        events = []
        emit_error(events.append, "bad")
        assert events[0]["type"] == "error"
        assert events[0]["message"] == "bad"


# ── format_project_rules ────────────────────────────────────

class TestFormatProjectRules:
    def test_none_input(self):
        assert format_project_rules(None, []) == ""

    def test_invalid_json(self):
        assert format_project_rules("not json", []) == ""

    def test_empty_list(self):
        assert format_project_rules("[]", ["a.py"]) == ""

    def test_enforce_rule(self):
        rules = [
            {"title": "No console.log", "description": "Remove debug logs", "ruleType": "ENFORCE", "filePatterns": []}
        ]
        result = format_project_rules(json.dumps(rules), ["app.js"])
        assert "No console.log" in result
        assert "ENFORCE" in result

    def test_suppress_rule(self):
        rules = [
            {"title": "Allow any", "description": "TS any is ok here", "ruleType": "SUPPRESS", "filePatterns": ["*.ts"]}
        ]
        result = format_project_rules(json.dumps(rules), ["app.ts"])
        assert "Allow any" in result
        assert "SUPPRESS" in result

    def test_file_pattern_mismatch(self):
        rules = [
            {"title": "Java only", "description": "test", "ruleType": "ENFORCE", "filePatterns": ["*.java"]}
        ]
        result = format_project_rules(json.dumps(rules), ["app.py"])
        assert result == ""

    def test_mixed_rules(self):
        rules = [
            {"title": "E1", "description": "d1", "ruleType": "ENFORCE", "filePatterns": []},
            {"title": "S1", "description": "d2", "ruleType": "SUPPRESS", "filePatterns": []},
        ]
        result = format_project_rules(json.dumps(rules), ["a.py"])
        assert "E1" in result
        assert "S1" in result


# ── format_project_rules_digest ──────────────────────────────

class TestFormatProjectRulesDigest:
    def test_none(self):
        assert format_project_rules_digest(None) == ""

    def test_invalid_json(self):
        assert format_project_rules_digest("{bad") == ""

    def test_valid(self):
        rules = [
            {"title": "Rule1", "ruleType": "ENFORCE"},
            {"title": "Rule2", "ruleType": "SUPPRESS"},
        ]
        result = format_project_rules_digest(json.dumps(rules))
        assert "[ENFORCE] Rule1" in result
        assert "[SUPPRESS] Rule2" in result
