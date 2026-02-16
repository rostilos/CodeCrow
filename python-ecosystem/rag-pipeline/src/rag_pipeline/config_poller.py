"""
Configuration polling from Java web-server's internal API.

In community/self-hosted mode, embedding configuration is managed via the
Site Admin panel and stored in the database. This module fetches that config
from the Java web-server's internal endpoint and sets the corresponding
environment variables before RAGConfig() reads them.

Flow:
  1. main.py calls fetch_and_apply_settings() before validate_environment()
  2. If CODECROW_WEB_SERVER_URL is set, we poll /api/internal/settings/embedding
  3. The response contains env-var-named keys, which we set as os.environ
  4. RAGConfig() then picks them up via its normal os.getenv() defaults

If CODECROW_WEB_SERVER_URL is NOT set, this module is a no-op (backward compatible).
"""
import os
import logging
import time
import httpx

logger = logging.getLogger(__name__)

_MAX_RETRIES = 10
_RETRY_DELAY_SECONDS = 5


def fetch_and_apply_settings() -> bool:
    """
    Fetch embedding settings from the Java web-server and apply them as env vars.

    Returns True if settings were successfully fetched and applied,
    False if polling is disabled or failed.
    """
    web_server_url = os.environ.get("CODECROW_WEB_SERVER_URL", "").rstrip("/")
    internal_secret = os.environ.get("CODECROW_INTERNAL_SECRET", "")

    if not web_server_url:
        logger.info("CODECROW_WEB_SERVER_URL not set — skipping config polling (using env vars)")
        return False

    endpoint = f"{web_server_url}/api/internal/settings/embedding"
    headers = {}
    if internal_secret:
        headers["X-Internal-Secret"] = internal_secret

    logger.info("Polling embedding config from %s", endpoint)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = httpx.get(endpoint, headers=headers, timeout=10.0)

            if resp.status_code == 200:
                config = resp.json()
                applied = _apply_config(config)
                if applied:
                    logger.info(
                        "Embedding config fetched and applied (attempt %d/%d): provider=%s",
                        attempt, _MAX_RETRIES, config.get("EMBEDDING_PROVIDER", "?")
                    )
                    return True
                else:
                    logger.warning("Config endpoint returned empty/incomplete config, retrying...")
            elif resp.status_code == 503 or resp.status_code == 404:
                logger.info(
                    "Web-server not ready yet (HTTP %d), retrying in %ds... (%d/%d)",
                    resp.status_code, _RETRY_DELAY_SECONDS, attempt, _MAX_RETRIES
                )
            else:
                logger.warning(
                    "Unexpected response from config endpoint: HTTP %d, body=%s",
                    resp.status_code, resp.text[:200]
                )
        except httpx.ConnectError:
            logger.info(
                "Cannot connect to web-server yet, retrying in %ds... (%d/%d)",
                _RETRY_DELAY_SECONDS, attempt, _MAX_RETRIES
            )
        except Exception as e:
            logger.warning(
                "Error fetching config (attempt %d/%d): %s",
                attempt, _MAX_RETRIES, str(e)
            )

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY_SECONDS)

    logger.warning(
        "Failed to fetch embedding config after %d attempts — falling back to env vars",
        _MAX_RETRIES
    )
    return False


def _apply_config(config: dict) -> bool:
    """
    Apply fetched config as environment variables.
    Only sets values that are non-empty. Does NOT override existing env vars
    that are already set (explicit env takes precedence).
    """
    if not config:
        return False

    # The keys returned by the endpoint match env var names exactly
    embedding_keys = [
        "EMBEDDING_PROVIDER",
        "OLLAMA_BASE_URL",
        "OLLAMA_EMBEDDING_MODEL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
    ]

    # Check if the provider is configured
    provider = config.get("EMBEDDING_PROVIDER", "")
    if not provider:
        return False

    applied_count = 0
    for key in embedding_keys:
        value = config.get(key, "")
        if value:
            # Only set if not already explicitly configured via env
            if not os.environ.get(key):
                os.environ[key] = value
                safe_val = _mask_secret(key, value)
                logger.debug("Set %s=%s (from web-server)", key, safe_val)
                applied_count += 1
            else:
                logger.debug("Skipping %s — already set in environment", key)

    return applied_count > 0 or bool(provider)


def _mask_secret(key: str, value: str) -> str:
    """Mask sensitive values for logging."""
    if "KEY" in key or "SECRET" in key:
        if len(value) > 10:
            return f"{value[:6]}...{value[-4:]}"
        return "****"
    return value
