import pytest

from magento2_benchmark.util import (
    configured_secret_values,
    public_config,
    redact_secret_text,
    require_no_secret_values,
)


def test_public_config_recursively_redacts_auth_and_cookie_values():
    config = {
        "custom_parameters": {
            "headers": {
                "Authorization": "Bearer secret",
                "Cookie": "session=secret",
                "X-Api-Key": "secret",
                "X-Trace": "safe",
            }
        }
    }

    value = public_config(config)

    headers = value["custom_parameters"]["headers"]
    assert headers["Authorization"] == "<redacted>"
    assert headers["Cookie"] == "<redacted>"
    assert headers["X-Api-Key"] == "<redacted>"
    assert headers["X-Trace"] == "safe"


def test_public_config_strips_url_userinfo_query_and_fragment():
    config = {
        "base_url": (
            "https://user:plain-secret@example.test:8443/v1"
            "?api_key=query-secret#private-fragment"
        )
    }

    value = public_config(config)

    assert value["base_url"] == "https://example.test:8443/v1"
    assert configured_secret_values(config) == {
        "plain-secret",
        "query-secret",
        "private-fragment",
    }


def test_external_artifacts_cannot_echo_configured_secrets():
    config = {
        "api_key_env": "KEY_ENV",
        "headers": {
            "Authorization": "Bearer exact-secret",
            "X-Trace": "safe",
        },
        "credentials": {"client": "client-secret"},
    }
    secrets = configured_secret_values(config)

    assert secrets == {
        "Bearer exact-secret",
        "client-secret",
    }
    require_no_secret_values({"response": "safe"}, secrets, context="fixture")
    with pytest.raises(RuntimeError, match="refusing to persist"):
        require_no_secret_values(
            {"response": "echo Bearer exact-secret"},
            secrets,
            context="fixture",
        )
    assert redact_secret_text(
        "provider echoed Bearer exact-secret",
        secrets,
    ) == "provider echoed <redacted>"
