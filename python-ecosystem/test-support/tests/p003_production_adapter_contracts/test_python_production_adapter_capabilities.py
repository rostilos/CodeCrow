from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest
from pydantic import BaseModel


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
for source_root in (
    REPOSITORY_ROOT / "python-ecosystem" / "test-support",
    REPOSITORY_ROOT / "python-ecosystem" / "inference-orchestrator" / "src",
    REPOSITORY_ROOT / "python-ecosystem" / "rag-pipeline" / "src",
):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from codecrow_test_harness import (  # noqa: E402
    ScenarioStep,
    ScriptedEmbeddingFake,
    ScriptedLlmFake,
    ScriptedScenario,
)
from codecrow_test_harness.environment import (  # noqa: E402
    CredentialReintroductionError,
)
from langchain_core.messages import AIMessage, AIMessageChunk  # noqa: E402
from llm import llm_factory as factory_module  # noqa: E402
from llm import ssrf_safe_transport  # noqa: E402
from llm.llm_factory import (  # noqa: E402
    ChatCloudflareOpenAI,
    ChatOpenRouter,
    LLMFactory,
)
from rag_pipeline.core.ollama_embedding import OllamaEmbedding  # noqa: E402
from rag_pipeline.core.openrouter_embedding import (  # noqa: E402
    OpenRouterEmbedding,
)
from p003_contract_support import (  # noqa: E402
    IMPLEMENTED_REVIEW_CAPABILITIES,
    Capability,
    FailureKind,
    HttpStep,
    MockHttpSequence,
    ScriptedHttpService,
    UnsupportedCapability,
    assert_failure_contract,
    assert_model_identity_contract,
    assert_overage_contract,
    assert_retry_ceiling_contract,
    assert_streaming_contract,
    assert_structured_output_contract,
    assert_unsupported_capability,
    close_adapter_clients,
    preserve_primary_error,
)


REQUIRED_REVIEW_CAPABILITIES = frozenset(
    {
        "streaming",
        "structured_output",
        "rate_limit_429",
        "malformed_payload",
        "timeout",
        "cancellation",
        "usage_overage",
        "retry_ceiling",
        "gemini_3_thinking_level",
        "vertex_adc",
        "vertex_express_key",
        "provider_headers",
        "cloudflare_payload_normalization",
        "embedding_model_identity",
        "embedding_empty_input",
        "embedding_partial_response",
        "embedding_dimension_mismatch",
        "embedding_dependency_failure",
        "primary_error_cleanup",
        "standalone_ledger_export",
    }
)


def test_preliminary_review_capability_expansion_is_complete() -> None:
    assert IMPLEMENTED_REVIEW_CAPABILITIES == REQUIRED_REVIEW_CAPABILITIES


def test_adapter_harness_exposes_an_explicit_absolute_ledger_path(
    adapter_harness: Any,
) -> None:
    assert adapter_harness.ledger_path.is_absolute()


LLM_PROMPT = "Return the deterministic contract response."
LLM_CONTENT = "offline production-adapter contract"
NORMAL_USAGE = {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
OVERAGE_USAGE = {"input_tokens": 4, "output_tokens": 17, "total_tokens": 21}


class StructuredContractResult(BaseModel):
    answer: str
    confidence: int


@dataclass(frozen=True, slots=True)
class LlmFamily:
    name: str
    provider: str
    model: str
    protocol: str
    request_path: str
    stream_path: str
    structured_method: str
    transport: str = "loopback"


@dataclass(slots=True)
class BuiltLlm:
    adapter: Any
    requests: Callable[[], tuple[Any, ...]]
    call_count: Callable[[], int]
    request_started: Any
    cleanup: Callable[[], None]


@dataclass(frozen=True, slots=True)
class EmbeddingFamily:
    name: str
    model: str


@dataclass(slots=True)
class BuiltEmbedding:
    adapter: Any
    service: ScriptedHttpService
    cleanup: Callable[[], None]


LLM_FAMILIES = (
    LlmFamily(
        "openai",
        "openai",
        "gpt-offline-contract",
        "openai",
        "/v1/chat/completions",
        "/v1/chat/completions",
        "json_mode",
    ),
    LlmFamily(
        "anthropic",
        "anthropic",
        "claude-offline-contract",
        "anthropic",
        "/v1/messages",
        "/v1/messages",
        "json_schema",
    ),
    LlmFamily(
        "gemini",
        "google",
        "gemini-offline-contract",
        "google",
        "/v1beta/models/gemini-offline-contract:generateContent",
        "/v1beta/models/gemini-offline-contract:streamGenerateContent?alt=sse",
        "json_schema",
    ),
    LlmFamily(
        "vertex",
        "google_vertex",
        "gemini-offline-contract",
        "google",
        "/v1beta1/projects/offline-project/locations/global/publishers/google/"
        "models/gemini-offline-contract:generateContent",
        "/v1beta1/projects/offline-project/locations/global/publishers/google/"
        "models/gemini-offline-contract:streamGenerateContent?alt=sse",
        "json_schema",
    ),
    LlmFamily(
        "openai-compatible",
        "openai_compatible",
        "compatible-offline-contract",
        "openai",
        "/v1/chat/completions",
        "/v1/chat/completions",
        "json_mode",
    ),
    LlmFamily(
        "cloudflare",
        "openai_compatible",
        "cloudflare-offline-contract",
        "openai",
        "/v1/offline/default/compat/chat/completions",
        "/v1/offline/default/compat/chat/completions",
        "json_mode",
        "mock",
    ),
    LlmFamily(
        "openrouter",
        "openrouter",
        "openrouter-offline-contract",
        "openai",
        "/api/v1/chat/completions",
        "/api/v1/chat/completions",
        "json_mode",
        "mock",
    ),
)

EMBEDDING_FAMILIES = (
    EmbeddingFamily("openrouter-embedding", "openai/offline-embedding"),
    EmbeddingFamily("ollama-embedding", "offline-embedding"),
)


@pytest.mark.parametrize("family", LLM_FAMILIES, ids=lambda family: family.name)
@pytest.mark.parametrize(
    "capability",
    (Capability.STREAMING, Capability.STRUCTURED_OUTPUT, Capability.USAGE_OVERAGE),
    ids=lambda capability: capability.value,
)
def test_supported_llm_capabilities_use_the_same_production_and_fake_assertion(
    family: LlmFamily,
    capability: Capability,
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _build_llm(family, capability, adapter_harness, monkeypatch)
    fake, scenario = _fake_for(capability, adapter_harness.ledger)
    with preserve_primary_error(built.cleanup):
        if capability == Capability.STREAMING:
            assert_streaming_contract(lambda: built.adapter.stream(LLM_PROMPT))
            assert_streaming_contract(lambda: fake.stream(LLM_PROMPT))
        elif capability == Capability.STRUCTURED_OUTPUT:
            production = built.adapter.with_structured_output(
                StructuredContractResult,
                method=family.structured_method,
            )
            scripted = fake.with_structured_output(StructuredContractResult)
            assert_structured_output_contract(lambda: production.invoke(LLM_PROMPT))
            assert_structured_output_contract(lambda: scripted.invoke(LLM_PROMPT))
        else:
            assert_overage_contract(
                lambda: built.adapter.invoke(LLM_PROMPT),
                token_budget=10,
            )
            assert_overage_contract(lambda: fake.invoke(LLM_PROMPT), token_budget=10)
        scenario.assert_consumed()
        assert built.call_count() == 1


@pytest.mark.parametrize("family", LLM_FAMILIES, ids=lambda family: family.name)
@pytest.mark.parametrize(
    "capability,expected",
    (
        (Capability.RATE_LIMIT, FailureKind.RATE_LIMIT),
        (Capability.MALFORMED_PAYLOAD, FailureKind.MALFORMED_PAYLOAD),
        (Capability.TIMEOUT, FailureKind.TIMEOUT),
    ),
    ids=lambda value: value.value,
)
def test_llm_failures_use_the_same_production_and_fake_assertion(
    family: LlmFamily,
    capability: Capability,
    expected: FailureKind,
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _build_llm(family, capability, adapter_harness, monkeypatch)
    fake, scenario = _fake_for(capability, adapter_harness.ledger)
    with preserve_primary_error(built.cleanup):
        assert_failure_contract(
            lambda: built.adapter.invoke(LLM_PROMPT),
            expected,
        )
        assert_failure_contract(_fake_failure_operation(fake, capability), expected)
        scenario.assert_consumed()
        assert built.call_count() == 1


@pytest.mark.parametrize("family", LLM_FAMILIES, ids=lambda family: family.name)
def test_llm_cancellation_uses_the_same_production_and_fake_assertion(
    family: LlmFamily,
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _build_llm(
        family,
        Capability.CANCELLATION,
        adapter_harness,
        monkeypatch,
    )
    fake, scenario = _fake_for(Capability.CANCELLATION, adapter_harness.ledger)
    with preserve_primary_error(built.cleanup):
        assert_failure_contract(
            lambda: asyncio.run(_cancel_after_request_start(built)),
            FailureKind.CANCELLATION,
        )
        assert_failure_contract(
            lambda: asyncio.run(fake.ainvoke(LLM_PROMPT)),
            FailureKind.CANCELLATION,
        )
        scenario.assert_consumed()
        assert built.call_count() == 1


@pytest.mark.parametrize("family", LLM_FAMILIES, ids=lambda family: family.name)
def test_llm_retry_ceiling_uses_the_same_production_and_fake_assertion(
    family: LlmFamily,
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built = _build_llm(
        family,
        Capability.RETRY_CEILING,
        adapter_harness,
        monkeypatch,
    )
    fake, scenario = _fake_for(Capability.RETRY_CEILING, adapter_harness.ledger)
    fake_call_count = 0

    def invoke_fake() -> Any:
        nonlocal fake_call_count
        fake_call_count += 1
        return fake.invoke(LLM_PROMPT)

    with preserve_primary_error(built.cleanup):
        assert_retry_ceiling_contract(
            lambda: built.adapter.invoke(LLM_PROMPT),
            built.call_count,
            maximum_calls=1,
        )
        assert_retry_ceiling_contract(
            invoke_fake,
            lambda: fake_call_count,
            maximum_calls=1,
        )
        scenario.assert_consumed()


@pytest.mark.parametrize("adapter", ("openrouter-embedding", "ollama-embedding"))
@pytest.mark.parametrize(
    "capability",
    (Capability.STREAMING, Capability.STRUCTURED_OUTPUT, Capability.USAGE_OVERAGE),
)
def test_embedding_only_capability_gaps_are_typed(
    adapter: str,
    capability: Capability,
) -> None:
    result = UnsupportedCapability(
        capability,
        adapter,
        "the embedding port returns vectors and exposes no chat-message capability",
    )
    assert_unsupported_capability(
        result,
        adapter=adapter,
        capability=capability,
    )


@pytest.mark.parametrize(
    "family",
    EMBEDDING_FAMILIES,
    ids=lambda family: family.name,
)
def test_embedding_model_identity_uses_the_same_production_and_fake_assertion(
    family: EmbeddingFamily,
    adapter_harness: Any,
) -> None:
    built = _build_embedding(family, "identity", adapter_harness)
    scenario = ScriptedScenario(f"{family.name}-identity", ())
    fake = ScriptedEmbeddingFake(
        scenario=scenario,
        ledger=adapter_harness.ledger,
        model=family.model,
        dimension=3,
    )
    with preserve_primary_error(built.cleanup):
        assert_model_identity_contract(built.adapter, family.model)
        assert_model_identity_contract(fake, family.model)
        scenario.assert_consumed()


@pytest.mark.parametrize(
    "family",
    EMBEDDING_FAMILIES,
    ids=lambda family: family.name,
)
def test_embedding_empty_input_uses_the_same_production_and_fake_assertion(
    family: EmbeddingFamily,
    adapter_harness: Any,
) -> None:
    built = _build_embedding(family, "empty", adapter_harness)
    scenario = ScriptedScenario(f"{family.name}-empty", ())
    fake = ScriptedEmbeddingFake(
        scenario=scenario,
        ledger=adapter_harness.ledger,
        model=family.model,
        dimension=3,
    )
    with preserve_primary_error(built.cleanup):
        assert_failure_contract(
            lambda: built.adapter.get_query_embedding(" "),
            FailureKind.EMPTY_INPUT,
        )
        assert_failure_contract(
            lambda: fake.get_query_embedding(" "),
            FailureKind.EMPTY_INPUT,
        )
        scenario.assert_consumed()


@pytest.mark.parametrize(
    "family",
    EMBEDDING_FAMILIES,
    ids=lambda family: family.name,
)
@pytest.mark.parametrize(
    "failure_kind",
    (FailureKind.DIMENSION_MISMATCH, FailureKind.DEPENDENCY),
)
def test_embedding_failures_use_the_same_production_and_fake_assertion(
    family: EmbeddingFamily,
    failure_kind: FailureKind,
    adapter_harness: Any,
) -> None:
    built = _build_embedding(family, failure_kind.value, adapter_harness)
    scenario_kind = (
        "response" if failure_kind == FailureKind.DIMENSION_MISMATCH else "retryable"
    )
    scenario = ScriptedScenario(
        f"{family.name}-{failure_kind.value}",
        (
            ScenarioStep(
                "embedding.query",
                1,
                scenario_kind,
                payload=[0.1, 0.2],
            ),
        ),
    )
    fake = ScriptedEmbeddingFake(
        scenario=scenario,
        ledger=adapter_harness.ledger,
        model=family.model,
        dimension=3,
    )
    with preserve_primary_error(built.cleanup):
        assert_failure_contract(
            lambda: built.adapter.get_query_embedding("query"),
            failure_kind,
        )
        assert_failure_contract(
            lambda: fake.get_query_embedding("query"),
            failure_kind,
        )
        scenario.assert_consumed()


def test_openrouter_partial_embedding_response_uses_the_same_fake_assertion(
    adapter_harness: Any,
) -> None:
    family = EMBEDDING_FAMILIES[0]
    built = _build_embedding(family, "partial_response", adapter_harness)
    scenario = ScriptedScenario(
        "openrouter-embedding-partial",
        (
            ScenarioStep(
                "embedding.batch",
                1,
                "response",
                payload=[[0.125, 0.25, 0.5]],
            ),
        ),
    )
    fake = ScriptedEmbeddingFake(
        scenario=scenario,
        ledger=adapter_harness.ledger,
        model=family.model,
        dimension=3,
    )
    with preserve_primary_error(built.cleanup):
        assert_failure_contract(
            lambda: built.adapter.get_text_embedding_batch(["first", "second"]),
            FailureKind.PARTIAL_RESPONSE,
        )
        assert_failure_contract(
            lambda: fake.get_text_embedding_batch(["first", "second"]),
            FailureKind.PARTIAL_RESPONSE,
        )
        scenario.assert_consumed()


def test_ollama_partial_embedding_cardinality_gap_is_typed_and_characterized(
    adapter_harness: Any,
) -> None:
    family = EMBEDDING_FAMILIES[1]
    built = _build_embedding(family, "partial_response", adapter_harness)
    with preserve_primary_error(built.cleanup):
        assert built.adapter.get_text_embedding_batch(["first", "second"]) == [
            [0.125, 0.25, 0.5]
        ]
        result = UnsupportedCapability(
            Capability.EMBEDDING_PARTIAL_RESPONSE,
            family.name,
            "the current production adapter does not reject a short /api/embed "
            "response; this legacy gap is characterized instead of fabricated",
        )
        assert_unsupported_capability(
            result,
            adapter=family.name,
            capability=Capability.EMBEDDING_PARTIAL_RESPONSE,
        )


def test_primary_test_failure_survives_multiple_cleanup_failures() -> None:
    def cleanup_one() -> None:
        raise RuntimeError("cleanup one")

    def cleanup_two() -> None:
        raise OSError("cleanup two")

    with pytest.raises(ValueError, match="primary assertion") as raised:
        with preserve_primary_error(cleanup_one, cleanup_two):
            raise ValueError("primary assertion")

    assert raised.value.__notes__ == [
        "suppressed test cleanup error: RuntimeError: cleanup one",
        "suppressed test cleanup error: OSError: cleanup two",
    ]


def test_cleanup_failure_is_primary_when_the_test_body_succeeds() -> None:
    def cleanup_one() -> None:
        raise RuntimeError("cleanup one")

    def cleanup_two() -> None:
        raise OSError("cleanup two")

    with pytest.raises(RuntimeError, match="cleanup one") as raised:
        with preserve_primary_error(cleanup_one, cleanup_two):
            pass

    assert raised.value.__notes__ == [
        "suppressed test cleanup error: OSError: cleanup two"
    ]


@pytest.mark.parametrize(
    "close_method",
    (OpenRouterEmbedding.close, OllamaEmbedding.close),
    ids=("openrouter", "ollama"),
)
def test_production_embedding_close_swallowing_is_explicitly_characterized(
    close_method: Callable[[Any], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingClient:
        def close(self) -> None:
            raise RuntimeError("production close failure")

    adapter = SimpleNamespace(_client=FailingClient())
    assert close_method(adapter) is None
    assert "Error closing" in caplog.text
    with pytest.raises(RuntimeError, match="production close failure"):
        close_adapter_clients(adapter)


def test_gemini_3_factory_sends_the_configured_thinking_level(
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = "gemini-3-offline-contract"
    path = f"/v1beta/models/{model}:generateContent"
    service = ScriptedHttpService(
        (
            HttpStep(
                "POST",
                path,
                headers={"content-type": "application/json"},
                body=_success_body("google", model, LLM_CONTENT, NORMAL_USAGE),
            ),
        ),
        ledger=adapter_harness.ledger,
        network_guard=adapter_harness.network,
        boundary="llm",
    ).start()
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", service.base_url)
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "medium")
    adapter = LLMFactory.create_llm(
        ai_model=model,
        ai_provider="google",
        ai_api_key="test",
        temperature=0.0,
    )
    with preserve_primary_error(lambda: _cleanup_adapter_and_service(adapter, service)):
        assert _content_text(adapter.invoke(LLM_PROMPT)) == LLM_CONTENT
        assert service.call_count == 1
        body = service.requests[0].body
        assert isinstance(body, dict)
        assert body["generationConfig"]["thinkingConfig"] == {
            "thinking_level": "MEDIUM"
        }


def test_vertex_adc_factory_route_uses_deterministic_application_credentials(
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google import auth as google_auth
    from google.auth.credentials import AnonymousCredentials

    credentials = AnonymousCredentials()
    credentials.token = "offline-adc-access-token"
    observed_scopes: list[tuple[str, ...]] = []

    def deterministic_default(
        *, scopes: list[str] | None = None, **_: Any
    ) -> tuple[Any, str]:
        observed_scopes.append(tuple(scopes or ()))
        return credentials, "offline-project"

    monkeypatch.setattr(google_auth, "default", deterministic_default)
    family = LLM_FAMILIES[3]
    service = ScriptedHttpService(
        (
            HttpStep(
                "POST",
                family.request_path,
                headers={"content-type": "application/json"},
                body=_success_body("google", family.model, LLM_CONTENT, NORMAL_USAGE),
            ),
        ),
        ledger=adapter_harness.ledger,
        network_guard=adapter_harness.network,
        boundary="llm",
    ).start()
    monkeypatch.setenv("GOOGLE_VERTEX_BASE_URL", service.base_url)
    adapter = LLMFactory.create_llm(
        ai_model=family.model,
        ai_provider="google_vertex",
        ai_api_key="adc",
        ai_base_url="offline-project/global",
        temperature=0.0,
    )
    with preserve_primary_error(lambda: _cleanup_adapter_and_service(adapter, service)):
        assert adapter.invoke(LLM_PROMPT).content == LLM_CONTENT
        assert observed_scopes == [
            ("https://www.googleapis.com/auth/cloud-platform",)
        ]
        assert service.requests[0].headers["authorization"] == (
            "Bearer offline-adc-access-token"
        )


def test_vertex_express_key_factory_route_is_typed_unsupported_under_offline_guard(
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    family = LLM_FAMILIES[3]
    calls_before = len(adapter_harness.ledger.entries)
    with pytest.raises(CredentialReintroductionError, match="GOOGLE_API_KEY"):
        LLMFactory.create_llm(
            ai_model=family.model,
            ai_provider="google_vertex",
            ai_api_key="vertex-express-test-key",
            ai_base_url=None,
            temperature=0.0,
        )
    result = UnsupportedCapability(
        Capability.VERTEX_EXPRESS_KEY,
        "google-vertex-express-key",
        "the pinned production SDK copies the key into GOOGLE_API_KEY; the offline "
        "credential guard rejects that mutation before any provider call",
    )
    assert_unsupported_capability(
        result,
        adapter="google-vertex-express-key",
        capability=Capability.VERTEX_EXPRESS_KEY,
    )
    assert len(adapter_harness.ledger.entries) == calls_before


def _build_embedding(
    family: EmbeddingFamily,
    behavior: str,
    harness: Any,
) -> BuiltEmbedding:
    steps: list[HttpStep] = []
    if family.name == "ollama-embedding":
        steps.append(
            HttpStep(
                "GET",
                "/api/tags",
                headers={"content-type": "application/json"},
                body={"models": [{"name": family.model}]},
            )
        )
    if behavior not in {"identity", "empty"}:
        if behavior == "partial_response":
            vectors = [[0.125, 0.25, 0.5]]
            steps.append(_embedding_step(family, vectors=vectors, batch=True))
        elif behavior == FailureKind.DIMENSION_MISMATCH.value:
            steps.append(_embedding_step(family, vectors=[[0.1, 0.2]]))
        elif behavior == FailureKind.DEPENDENCY.value:
            steps.append(
                _embedding_step(
                    family,
                    vectors=[],
                    status=503,
                )
            )
        else:
            raise AssertionError(f"unknown embedding behavior: {behavior}")
    service = ScriptedHttpService(
        steps,
        ledger=harness.ledger,
        network_guard=harness.network,
        boundary="embedding",
    ).start()
    try:
        if family.name == "openrouter-embedding":
            adapter = OpenRouterEmbedding(
                api_key="test",
                model=family.model,
                api_base=f"{service.base_url}/v1",
                timeout=2.0,
                max_retries=0,
                embed_batch_size=8,
                expected_dim=3,
            )
        else:
            adapter = OllamaEmbedding(
                model=family.model,
                base_url=service.base_url,
                timeout=2.0,
                embed_batch_size=8,
                expected_dim=3,
                max_retries=0,
                retry_base_delay=0.0,
            )
    except BaseException:
        service.stop()
        raise
    return BuiltEmbedding(
        adapter,
        service,
        lambda: _cleanup_adapter_and_service(adapter, service),
    )


def _embedding_step(
    family: EmbeddingFamily,
    *,
    vectors: list[list[float]],
    batch: bool = False,
    status: int = 200,
) -> HttpStep:
    if family.name == "openrouter-embedding":
        body: dict[str, Any]
        if status == 200:
            body = {
                "object": "list",
                "model": family.model,
                "data": [
                    {
                        "object": "embedding",
                        "index": index,
                        "embedding": vector,
                    }
                    for index, vector in enumerate(vectors)
                ],
            }
        else:
            body = {
                "error": {
                    "message": "embedding dependency unavailable",
                    "type": "server_error",
                    "code": "503",
                }
            }
        return HttpStep(
            "POST",
            "/v1/embeddings",
            status=status,
            headers={"content-type": "application/json"},
            body=body,
        )
    path = "/api/embed" if batch else "/api/embeddings"
    if status != 200:
        body = {"error": "embedding dependency unavailable"}
    elif batch:
        body = {"embeddings": vectors}
    else:
        body = {"embedding": vectors[0]}
    return HttpStep(
        "POST",
        path,
        status=status,
        headers={"content-type": "application/json"},
        body=body,
    )


def _build_llm(
    family: LlmFamily,
    capability: Capability,
    harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> BuiltLlm:
    step = _http_step(family, capability)
    timeout = 0.02 if capability == Capability.TIMEOUT else 2.0
    if family.transport == "mock":
        sequence = MockHttpSequence(
            (step,),
            ledger=harness.ledger,
            boundary="llm",
            target=(
                "openrouter.ai:443"
                if family.name == "openrouter"
                else "gateway.ai.cloudflare.com:443"
            ),
        )
        sync_http = httpx.Client(transport=httpx.MockTransport(sequence.sync_handler))
        async_http = httpx.AsyncClient(
            transport=httpx.MockTransport(sequence.async_handler)
        )
        if family.name == "openrouter":
            original_init = ChatOpenRouter.__init__

            def openrouter_init(
                self: ChatOpenRouter,
                api_key: str | None = None,
                **kwargs: Any,
            ) -> None:
                kwargs.update(
                    http_client=sync_http,
                    http_async_client=async_http,
                    max_retries=0,
                    timeout=timeout,
                )
                original_init(self, api_key=api_key, **kwargs)

            monkeypatch.setattr(ChatOpenRouter, "__init__", openrouter_init)
            adapter = LLMFactory.create_llm(
                ai_model=family.model,
                ai_provider=family.provider,
                ai_api_key="test",
                temperature=0.0,
            )
        else:
            monkeypatch.setattr(
                ssrf_safe_transport,
                "create_ssrf_safe_http_client",
                lambda _base_url: sync_http,
            )
            monkeypatch.setattr(
                ssrf_safe_transport,
                "create_ssrf_safe_async_http_client",
                lambda _base_url: async_http,
            )
            adapter = LLMFactory.create_llm(
                ai_model=family.model,
                ai_provider=family.provider,
                ai_api_key="test",
                ai_base_url=(
                    "https://gateway.ai.cloudflare.com/v1/offline/default/compat"
                ),
                temperature=0.0,
                ai_custom_parameters={"max_retries": 0, "request_timeout": timeout},
            )
            assert isinstance(adapter, ChatCloudflareOpenAI)
        return BuiltLlm(
            adapter,
            lambda: sequence.requests,
            lambda: sequence.call_count,
            sequence.request_started,
            lambda: close_adapter_clients(adapter),
        )

    service = ScriptedHttpService(
        (step,),
        ledger=harness.ledger,
        network_guard=harness.network,
        boundary="llm",
    ).start()
    try:
        if family.name == "openai":
            adapter = factory_module.ChatOpenAI(
                api_key="test",
                model=family.model,
                base_url=f"{service.base_url}/v1",
                temperature=0.0,
                model_kwargs={"parallel_tool_calls": False},
                max_retries=0,
                timeout=timeout,
            )
        elif family.name == "anthropic":
            adapter = factory_module.ChatAnthropic(
                api_key="test",
                model=family.model,
                base_url=service.base_url,
                temperature=0.0,
                model_kwargs={
                    "tool_choice": {
                        "type": "auto",
                        "disable_parallel_tool_use": True,
                    }
                },
                max_retries=0,
                timeout=timeout,
            )
        elif family.name == "gemini":
            monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", service.base_url)
            adapter = factory_module.ChatGoogleGenerativeAI(
                google_api_key="test",
                model=family.model,
                temperature=0.0,
                thinking_budget=0,
                max_retries=1,
                timeout=timeout,
            )
        elif family.name == "vertex":
            from google.auth.credentials import AnonymousCredentials

            monkeypatch.setenv("GOOGLE_VERTEX_BASE_URL", service.base_url)
            credentials = AnonymousCredentials()
            credentials.token = "offline-test-access-token"
            adapter = factory_module.ChatGoogleGenerativeAI(
                model=family.model,
                vertexai=True,
                temperature=0.0,
                project="offline-project",
                location="global",
                credentials=credentials,
                max_retries=1,
                timeout=timeout,
            )
        else:
            monkeypatch.setattr(ssrf_safe_transport, "_ALLOW_PRIVATE", True)
            adapter = LLMFactory.create_llm(
                ai_model=family.model,
                ai_provider=family.provider,
                ai_api_key="test",
                ai_base_url=service.base_url,
                temperature=0.0,
                ai_custom_parameters={"max_retries": 0, "request_timeout": timeout},
            )
    except BaseException:
        service.stop()
        raise

    def cleanup() -> None:
        with preserve_primary_error(service.stop):
            close_adapter_clients(adapter)

    return BuiltLlm(
        adapter,
        lambda: service.requests,
        lambda: service.call_count,
        service.request_started,
        cleanup,
    )


def _cleanup_adapter_and_service(adapter: Any, service: ScriptedHttpService) -> None:
    with preserve_primary_error(service.stop):
        close_adapter_clients(adapter)


def _http_step(family: LlmFamily, capability: Capability) -> HttpStep:
    path = (
        family.stream_path
        if capability == Capability.STREAMING
        else family.request_path
    )
    if capability == Capability.STREAMING:
        return HttpStep(
            "POST",
            path,
            headers={"content-type": "text/event-stream"},
            raw_body=_stream_body(family.protocol, family.model),
        )
    if capability == Capability.RATE_LIMIT:
        return HttpStep(
            "POST",
            path,
            status=429,
            headers={"content-type": "application/json", "retry-after": "7"},
            body=_error_body(family.protocol, 429, "rate limit"),
        )
    if capability == Capability.MALFORMED_PAYLOAD:
        return HttpStep(
            "POST",
            path,
            headers={"content-type": "application/json"},
            raw_body=b'{"malformed":',
        )
    if capability == Capability.TIMEOUT:
        return HttpStep(
            "POST",
            path,
            headers={"content-type": "application/json"},
            body=_success_body(
                family.protocol,
                family.model,
                LLM_CONTENT,
                NORMAL_USAGE,
            ),
            delay_seconds=0.1,
            transport_error=(
                FailureKind.TIMEOUT if family.transport == "mock" else None
            ),
        )
    if capability == Capability.CANCELLATION:
        return HttpStep(
            "POST",
            path,
            headers={"content-type": "application/json"},
            body=_success_body(
                family.protocol,
                family.model,
                LLM_CONTENT,
                NORMAL_USAGE,
            ),
            delay_seconds=0.5,
        )
    if capability == Capability.RETRY_CEILING:
        return HttpStep(
            "POST",
            path,
            status=503,
            headers={"content-type": "application/json"},
            body=_error_body(family.protocol, 503, "dependency unavailable"),
        )
    content = (
        json.dumps({"answer": "offline", "confidence": 7})
        if capability == Capability.STRUCTURED_OUTPUT
        else LLM_CONTENT
    )
    usage = OVERAGE_USAGE if capability == Capability.USAGE_OVERAGE else NORMAL_USAGE
    return HttpStep(
        "POST",
        path,
        headers={"content-type": "application/json"},
        body=_success_body(family.protocol, family.model, content, usage),
    )


def _success_body(
    protocol: str,
    model: str,
    content: str,
    usage: dict[str, int],
) -> dict[str, Any]:
    if protocol == "anthropic":
        return {
            "id": "msg_offline_capability",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": content}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
            },
        }
    if protocol == "google":
        return {
            "candidates": [
                {
                    "content": {"parts": [{"text": content}], "role": "model"},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {
                "promptTokenCount": usage["input_tokens"],
                "candidatesTokenCount": usage["output_tokens"],
                "totalTokenCount": usage["total_tokens"],
            },
            "modelVersion": model,
        }
    return {
        "id": "chatcmpl-offline-capability",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage["input_tokens"],
            "completion_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
        },
    }


def _error_body(protocol: str, status: int, message: str) -> dict[str, Any]:
    if protocol == "anthropic":
        return {
            "type": "error",
            "error": {"type": "rate_limit_error", "message": message},
        }
    if protocol == "google":
        return {
            "error": {
                "code": status,
                "message": message,
                "status": (
                    "RESOURCE_EXHAUSTED" if status == 429 else "UNAVAILABLE"
                ),
            }
        }
    return {
        "error": {
            "message": message,
            "type": "rate_limit_error" if status == 429 else "server_error",
            "code": str(status),
        }
    }


def _stream_body(protocol: str, model: str) -> bytes:
    if protocol == "anthropic":
        events = (
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_stream_contract",
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 2, "output_tokens": 0},
                    },
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "offline "},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": "production-adapter contract",
                    },
                },
            ),
            (
                "content_block_stop",
                {"type": "content_block_stop", "index": 0},
            ),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 3},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        )
        return "".join(
            f"event: {event}\ndata: {json.dumps(payload)}\n\n"
            for event, payload in events
        ).encode()
    if protocol == "google":
        payloads = (
            _success_body(protocol, model, "offline ", NORMAL_USAGE),
            _success_body(
                protocol,
                model,
                "production-adapter contract",
                NORMAL_USAGE,
            ),
        )
        return "".join(
            f"data: {json.dumps(payload)}\n\n" for payload in payloads
        ).encode()
    payloads = (
        {
            "id": "chatcmpl-stream-contract",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "offline "},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-stream-contract",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "production-adapter contract"},
                    "finish_reason": "stop",
                }
            ],
        },
    )
    return (
        "".join(f"data: {json.dumps(payload)}\n\n" for payload in payloads)
        + "data: [DONE]\n\n"
    ).encode()


def _fake_for(
    capability: Capability,
    ledger: Any,
) -> tuple[ScriptedLlmFake, ScriptedScenario]:
    operation = "llm.ainvoke" if capability == Capability.CANCELLATION else (
        "llm.stream" if capability == Capability.STREAMING else "llm.invoke"
    )
    if capability == Capability.STREAMING:
        step = ScenarioStep(
            operation,
            1,
            "stream",
            chunks=(
                AIMessageChunk(content="offline "),
                AIMessageChunk(content="production-adapter contract"),
            ),
        )
    elif capability == Capability.STRUCTURED_OUTPUT:
        step = ScenarioStep(
            operation,
            1,
            "structured",
            payload=StructuredContractResult(answer="offline", confidence=7),
        )
    elif capability == Capability.USAGE_OVERAGE:
        step = ScenarioStep(
            operation,
            1,
            "overage",
            payload=AIMessage(content=LLM_CONTENT, usage_metadata=OVERAGE_USAGE),
            usage=OVERAGE_USAGE,
        )
    elif capability == Capability.RATE_LIMIT:
        step = ScenarioStep(operation, 1, "rate_limit", retry_after_seconds=7)
    elif capability == Capability.MALFORMED_PAYLOAD:
        step = ScenarioStep(operation, 1, "malformed", payload="malformed payload")
    elif capability == Capability.TIMEOUT:
        step = ScenarioStep(operation, 1, "timeout")
    elif capability == Capability.CANCELLATION:
        step = ScenarioStep(operation, 1, "cancellation")
    else:
        step = ScenarioStep(operation, 1, "retryable")
    scenario = ScriptedScenario(f"{capability.value}-parity", (step,))
    return ScriptedLlmFake(scenario=scenario, ledger=ledger), scenario


def _fake_failure_operation(
    fake: ScriptedLlmFake,
    capability: Capability,
) -> Callable[[], Any]:
    if capability != Capability.MALFORMED_PAYLOAD:
        return lambda: fake.invoke(LLM_PROMPT)

    def malformed() -> None:
        result = fake.invoke(LLM_PROMPT)
        if not isinstance(result, AIMessage):
            raise ValueError("malformed payload from scripted LLM fake")

    return malformed


async def _cancel_after_request_start(built: BuiltLlm) -> Any:
    task = asyncio.create_task(built.adapter.ainvoke(LLM_PROMPT))
    started = await asyncio.to_thread(built.request_started.wait, 1.0)
    assert started, "production adapter never reached its test-owned transport"
    task.cancel()
    return await task


def _content_text(message: Any) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict)
    )
