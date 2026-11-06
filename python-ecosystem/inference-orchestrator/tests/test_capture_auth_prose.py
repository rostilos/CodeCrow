"""Actual Tauri8 prompt said basic formatting; capture replaced formatting."""
import pytest

from service.review.quality_capture import ReviewQualityCaptureSession, _redact_secrets


def scrub(value):
    session = object.__new__(ReviewQualityCaptureSession)
    session._credential_values = ["request-secret"]
    return session._scrub_known_credentials(_redact_secrets(value))


@pytest.mark.parametrize("text", [
    "**Objective:** Enforce consistent basic formatting across all file types.",
    "Use Basic examples and basic validation before complex checks.",
])
def test_capture_preserves_ordinary_basic_prose(text):
    assert scrub(text) == text


@pytest.mark.parametrize("text,secret", [
    ("Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ("Use Basic dXNlcjpwYXNz.", "dXNlcjpwYXNz"),
    ("bAsIc dXNlcjpwYXNz", "dXNlcjpwYXNz"),
    ("Authorization: Basic opaque-token", "opaque-token"),
    ('{"aUtHoRiZaTiOn": "bAsIc opaque-token"}', "opaque-token"),
    ('{\\"Authorization\\": \\"Basic opaque-token\\"}', "opaque-token"),
    ("Proxy-Authorization=Basic opaque-token", "opaque-token"),
    ("Bearer opaque-token", "opaque-token"),
    ("Known request-secret in source", "request-secret"),
])
def test_capture_keeps_redacting_real_credentials(text, secret):
    result = scrub(text)
    assert secret not in result
    assert "[REDACTED]" in result


def test_capture_preserves_structured_case_insensitive_header_redaction():
    assert scrub({"aUtHoRiZaTiOn": "Basic nonstandard-token"}) == {"aUtHoRiZaTiOn": "[REDACTED]"}
