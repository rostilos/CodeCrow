"""
Tests for rag_pipeline.config_poller — fetch_and_apply_settings, _apply_config, _mask_secret.
"""
import os
import pytest
from unittest.mock import patch, MagicMock

from rag_pipeline.config_poller import (
    fetch_and_apply_settings,
    _apply_config,
    _mask_secret,
)


# ─────────────────────────────────────────────────────────────
# _mask_secret
# ─────────────────────────────────────────────────────────────
class TestMaskSecret:

    def test_masks_key_field(self):
        result = _mask_secret("OPENROUTER_API_KEY", "sk-1234567890abcdef")
        assert result == "****"

    def test_masks_secret_field(self):
        result = _mask_secret("SERVICE_SECRET", "mysuperlongsecret")
        assert result == "****"

    def test_short_secret_returns_stars(self):
        result = _mask_secret("API_KEY", "short")
        assert result == "****"

    def test_non_secret_key_returns_value(self):
        result = _mask_secret("EMBEDDING_PROVIDER", "ollama")
        assert result == "ollama"


# ─────────────────────────────────────────────────────────────
# _apply_config
# ─────────────────────────────────────────────────────────────
class TestApplyConfig:

    def test_empty_config_returns_false(self):
        assert _apply_config({}) is False
        assert _apply_config(None) is False

    def test_no_provider_returns_false(self):
        assert _apply_config({"OLLAMA_BASE_URL": "http://localhost:11434"}) is False

    def test_web_server_defaults_do_not_override_deployment_fallback(self):
        os.environ["EMBEDDING_PROVIDER"] = "openrouter"
        os.environ["OPENROUTER_API_KEY"] = "deployment-key"
        config = {
            "EMBEDDING_SETTINGS_CONFIGURED": "false",
            "EMBEDDING_PROVIDER": "ollama",
            "OPENROUTER_API_KEY": "",
        }

        assert _apply_config(config) is False
        assert os.environ["EMBEDDING_PROVIDER"] == "openrouter"
        assert os.environ["OPENROUTER_API_KEY"] == "deployment-key"

        os.environ.pop("EMBEDDING_PROVIDER", None)
        os.environ.pop("OPENROUTER_API_KEY", None)

    def test_sets_env_vars(self):
        config = {
            "EMBEDDING_SETTINGS_CONFIGURED": "true",
            "EMBEDDING_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": "http://localhost:11434",
            "OLLAMA_EMBEDDING_MODEL": "nomic-embed-text",
        }
        # Remove env vars if present
        for k in config:
            os.environ.pop(k, None)

        result = _apply_config(config)
        assert result is True
        assert os.environ.get("EMBEDDING_PROVIDER") == "ollama"
        assert os.environ.get("OLLAMA_BASE_URL") == "http://localhost:11434"

        # Cleanup
        for k in config:
            os.environ.pop(k, None)

    def test_dashboard_provider_overrides_local_fallback(self):
        os.environ["EMBEDDING_PROVIDER"] = "openrouter"
        config = {
            "EMBEDDING_SETTINGS_CONFIGURED": "true",
            "EMBEDDING_PROVIDER": "ollama",
        }
        result = _apply_config(config)
        assert os.environ.get("EMBEDDING_PROVIDER") == "ollama"
        assert result is True

        # Cleanup
        os.environ.pop("EMBEDDING_PROVIDER", None)

    def test_empty_dashboard_values_do_not_erase_fallback_credentials(self):
        os.environ["OPENROUTER_API_KEY"] = "fallback-key"
        config = {
            "EMBEDDING_PROVIDER": "openrouter",
            "OPENROUTER_API_KEY": "",
        }

        assert _apply_config(config) is True
        assert os.environ["OPENROUTER_API_KEY"] == "fallback-key"

        os.environ.pop("EMBEDDING_PROVIDER", None)
        os.environ.pop("OPENROUTER_API_KEY", None)


# ─────────────────────────────────────────────────────────────
# fetch_and_apply_settings
# ─────────────────────────────────────────────────────────────
class TestFetchAndApplySettings:

    def test_no_web_server_url_returns_false(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODECROW_WEB_SERVER_URL", None)
            result = fetch_and_apply_settings()
            assert result is False

    @patch("rag_pipeline.config_poller.httpx.get")
    def test_success_on_first_try(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "EMBEDDING_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": "http://ollama:11434",
            "OLLAMA_EMBEDDING_MODEL": "nomic-embed-text",
        }
        mock_get.return_value = mock_resp

        # Clean env to allow setting
        for k in ("EMBEDDING_PROVIDER", "OLLAMA_BASE_URL", "OLLAMA_EMBEDDING_MODEL"):
            os.environ.pop(k, None)

        with patch.dict(os.environ, {"CODECROW_WEB_SERVER_URL": "http://web:8080"}):
            result = fetch_and_apply_settings()

        assert result is True
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "http://web:8080/api/internal/settings/embedding" == call_args[0][0]

        # Cleanup
        for k in ("EMBEDDING_PROVIDER", "OLLAMA_BASE_URL", "OLLAMA_EMBEDDING_MODEL"):
            os.environ.pop(k, None)

    @patch("rag_pipeline.config_poller.httpx.get")
    def test_unsaved_web_defaults_accept_deployment_fallback_without_retry(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "EMBEDDING_SETTINGS_CONFIGURED": "false",
            "EMBEDDING_PROVIDER": "ollama",
        }
        mock_get.return_value = mock_resp

        with patch.dict(os.environ, {
            "CODECROW_WEB_SERVER_URL": "http://web:8080",
            "EMBEDDING_PROVIDER": "openrouter",
        }):
            result = fetch_and_apply_settings()
            assert os.environ["EMBEDDING_PROVIDER"] == "openrouter"

        assert result is True
        mock_get.assert_called_once()

    @patch("rag_pipeline.config_poller.httpx.get")
    def test_uses_internal_secret_header(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"EMBEDDING_PROVIDER": "ollama"}
        mock_get.return_value = mock_resp

        os.environ.pop("EMBEDDING_PROVIDER", None)

        with patch.dict(os.environ, {
            "CODECROW_WEB_SERVER_URL": "http://web:8080",
            "CODECROW_INTERNAL_SECRET": "my-secret",
        }):
            fetch_and_apply_settings()

        call_kwargs = mock_get.call_args
        headers = call_kwargs[1]["headers"] if "headers" in call_kwargs[1] else call_kwargs.kwargs.get("headers", {})
        assert headers.get("X-Internal-Secret") == "my-secret"

        os.environ.pop("EMBEDDING_PROVIDER", None)

    @patch("rag_pipeline.config_poller.httpx.get")
    @patch("rag_pipeline.config_poller.time.sleep")
    def test_retries_on_503(self, mock_sleep, mock_get):
        resp_503 = MagicMock(status_code=503, text="Not ready")
        resp_200 = MagicMock(status_code=200)
        resp_200.json.return_value = {"EMBEDDING_PROVIDER": "ollama"}
        mock_get.side_effect = [resp_503, resp_503, resp_200]

        os.environ.pop("EMBEDDING_PROVIDER", None)

        with patch.dict(os.environ, {"CODECROW_WEB_SERVER_URL": "http://web:8080"}):
            result = fetch_and_apply_settings()

        assert result is True
        assert mock_get.call_count == 3
        assert mock_sleep.call_count == 2

        os.environ.pop("EMBEDDING_PROVIDER", None)

    @patch("rag_pipeline.config_poller.httpx.get")
    @patch("rag_pipeline.config_poller.time.sleep")
    def test_returns_false_after_max_retries(self, mock_sleep, mock_get):
        import httpx
        mock_get.side_effect = httpx.ConnectError("Connection refused")

        with patch.dict(os.environ, {"CODECROW_WEB_SERVER_URL": "http://web:8080"}):
            result = fetch_and_apply_settings()

        assert result is False
        assert mock_get.call_count == 10  # _MAX_RETRIES
