"""Invocation failures are separate from scheduling, output size, and transport configuration."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from llm.review_invocation import _call_timeout_seconds, invoke_review_model
from llm.openai_parameters import _openrouter_custom_extra_body


def test_default_router_speed_preference_preserves_all_explicit_constraints():
    assert _openrouter_custom_extra_body(None) == {"provider": {"sort": "throughput"}}
    privacy = {"ignore": ["provider-a"], "quantizations": ["bf16"], "data_collection": "deny", "allow_fallbacks": False}
    assert _openrouter_custom_extra_body({"provider": privacy}) == {"provider": {**privacy, "sort": "throughput"}}
    for explicit in ({"order": ["cloudflare"]}, {"only": ["cloudflare"]}, {"sort": "latency"}, {"order": []}):
        configured = {**privacy, **explicit}
        assert _openrouter_custom_extra_body({"extra_body": {"provider": configured}}) == {"provider": configured}


def test_elapsed_deadline_is_configurable_separately_from_socket_inactivity(monkeypatch):
    monkeypatch.delenv("REVIEW_MODEL_CALL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("LLM_PROVIDER_TIMEOUT_SECONDS", "45")
    assert _call_timeout_seconds() == 900
    monkeypatch.setenv("REVIEW_MODEL_CALL_TIMEOUT_SECONDS", "1800.5")
    assert _call_timeout_seconds() == 1800.5
    monkeypatch.setenv("REVIEW_MODEL_CALL_TIMEOUT_SECONDS", "nan")
    assert _call_timeout_seconds() == 900


@pytest.mark.asyncio
async def test_earlier_explicit_transport_timeout_is_not_relabelled_as_elapsed_deadline():
    error = TimeoutError("Explicit upstream operation timeout")
    model = SimpleNamespace(ainvoke=AsyncMock(side_effect=error))
    with pytest.raises(TimeoutError) as caught:
        await invoke_review_model(model, [], request=SimpleNamespace(), stage="discovery", options={"timeout": 45})
    assert caught.value is error
    assert model.ainvoke.call_args.kwargs == {"timeout": 45}
