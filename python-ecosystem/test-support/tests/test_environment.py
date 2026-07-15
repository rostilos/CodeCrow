from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import pytest


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.environment import (
    TEST_SERVICE_SECRET,
    CredentialReintroductionError,
    CredentialScrubber,
)


def test_scrubber_clears_credentials_blocks_dotenv_reintroduction_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "preexisting-real-looking-value")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("SERVICE_SECRET", "preexisting-service-secret")
    original_environment = os.environ

    with CredentialScrubber() as scrubber:
        assert os.environ is not original_environment
        assert os.environ["OPENAI_API_KEY"] == ""
        assert os.environ["ANTHROPIC_API_KEY"] == ""
        assert os.environ["SERVICE_SECRET"] == TEST_SERVICE_SECRET
        scrubber.assert_sanitized()
        with pytest.raises(CredentialReintroductionError, match="OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = "loaded-by-dotenv"
        with pytest.raises(CredentialReintroductionError, match="SERVICE_SECRET"):
            os.environ["SERVICE_SECRET"] = "loaded-by-dotenv"
        os.environ["OPENAI_API_KEY"] = ""
        os.environ["SERVICE_SECRET"] = TEST_SERVICE_SECRET
        os.environ["P003_NON_SECRET"] = "value"
        assert len(os.environ) > 0
        assert "P003_NON_SECRET" in iter(os.environ)
        del os.environ["P003_NON_SECRET"]
        assert os.environ.copy()["SERVICE_SECRET"] == TEST_SERVICE_SECRET

    assert os.environ is original_environment
    assert os.environ["OPENAI_API_KEY"] == "preexisting-real-looking-value"
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["SERVICE_SECRET"] == "preexisting-service-secret"


def test_custom_environment_detects_reintroduction_and_nested_scrubbers() -> None:
    environment = {"OPENAI_API_KEY": "before", "SERVICE_SECRET": "before-service"}
    scrubber = CredentialScrubber(environment)
    with scrubber:
        environment["OPENAI_API_KEY"] = "reintroduced"
        environment["SERVICE_SECRET"] = "wrong"
        with pytest.raises(CredentialReintroductionError, match="OPENAI_API_KEY, SERVICE_SECRET"):
            scrubber.assert_sanitized()
        with pytest.raises(RuntimeError, match="already active"):
            CredentialScrubber({}).__enter__()
    scrubber.__exit__(None, None, None)
    assert environment == {
        "OPENAI_API_KEY": "before",
        "SERVICE_SECRET": "before-service",
    }


def test_non_populating_mode_preserves_absence_and_approved_component_fixtures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SERVICE_SECRET", raising=False)
    monkeypatch.setenv("CODECROW_INTERNAL_SECRET", "ambient-real-looking-secret")

    with CredentialScrubber(populate_service_secrets=False) as scrubber:
        assert "SERVICE_SECRET" not in os.environ
        assert os.environ["CODECROW_INTERNAL_SECRET"] == TEST_SERVICE_SECRET
        os.environ["SERVICE_SECRET"] = "test-secret"
        os.environ["CODECROW_INTERNAL_SECRET"] = "my-secret"
        scrubber.assert_sanitized()
        with pytest.raises(CredentialReintroductionError, match="SERVICE_SECRET"):
            os.environ["SERVICE_SECRET"] = "loaded-by-dotenv"


def test_cached_putenv_and_environb_cannot_reintroduce_native_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "OPENAI_API_KEY"
    encoded_key = os.fsencode(key)
    cached_putenv = os.putenv
    cached_unsetenv = os.unsetenv
    cached_environb = os.environb
    libc = ctypes.CDLL(None)
    libc.getenv.argtypes = [ctypes.c_char_p]
    libc.getenv.restype = ctypes.c_char_p
    monkeypatch.delenv(key, raising=False)

    with CredentialScrubber() as scrubber:
        assert libc.getenv(encoded_key) in (None, b"")
        with pytest.raises(CredentialReintroductionError, match=key):
            cached_putenv(key, "cached-bypass")
        with pytest.raises(CredentialReintroductionError, match=key):
            cached_environb[encoded_key] = b"bytes-bypass"
        assert libc.getenv(encoded_key) in (None, b"")
        scrubber.assert_sanitized()

    try:
        cached_putenv(key, "audit-hook-is-inert-after-exit")
        assert libc.getenv(encoded_key) == b"audit-hook-is-inert-after-exit"
    finally:
        cached_unsetenv(key)


def test_known_fixture_credentials_are_allowed_only_ephemerally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    with CredentialScrubber() as scrubber:
        for fixture_value in ("key", "test", "test-key"):
            os.environ["AI_API_KEY"] = fixture_value
            assert os.environ["AI_API_KEY"] == fixture_value
            os.environ["AI_API_KEY"] = ""
        scrubber.assert_sanitized()
        with pytest.raises(CredentialReintroductionError, match="AI_API_KEY"):
            os.environ["AI_API_KEY"] = "real-looking-provider-secret"


@pytest.mark.parametrize(
    "key",
    [
        "AI_API_KEY",
        "QA_DOC_AI_API_KEY",
        "QDRANT_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "LANGSMITH_API_KEY",
        "HF_TOKEN",
        "COHERE_API_KEY",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "DEEPSEEK_API_KEY",
    ],
)
def test_provider_and_sdk_credential_inventory_is_scrubbed(
    key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(key, "ambient-real-looking-value")
    with CredentialScrubber() as scrubber:
        assert os.environ[key] == ""
        scrubber.assert_sanitized()
