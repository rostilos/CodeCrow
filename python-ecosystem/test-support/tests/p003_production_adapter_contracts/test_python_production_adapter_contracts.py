from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import httpx
import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
for source_root in (
    REPOSITORY_ROOT / "python-ecosystem" / "test-support",
    REPOSITORY_ROOT / "python-ecosystem" / "inference-orchestrator" / "src",
    REPOSITORY_ROOT / "python-ecosystem" / "rag-pipeline" / "src",
):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from codecrow_test_harness import (  # noqa: E402
    ExternalCallLedger,
    NetworkDenyGuard,
    ProtocolCall,
    ProtocolFixtureServer,
    ScenarioStep,
    ScriptedEmbeddingFake,
    ScriptedLlmFake,
    ScriptedScenario,
    UnexpectedExternalCall,
)
from langchain_core.messages import AIMessage  # noqa: E402
from llm import ssrf_safe_transport  # noqa: E402
from llm.llm_factory import (  # noqa: E402
    ChatCloudflareOpenAI,
    ChatOpenRouter,
    LLMFactory,
)
from rag_pipeline.core.ollama_embedding import OllamaEmbedding  # noqa: E402
from rag_pipeline.core.openrouter_embedding import OpenRouterEmbedding  # noqa: E402
from p003_contract_support import (  # noqa: E402
    close_adapter_clients,
    preserve_primary_error,
)


FIXTURES = Path(__file__).parent / "fixtures"
LLM_PROMPT = "Return the deterministic contract response."
LLM_CONTENT = "offline production-adapter contract"
LLM_USAGE = {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
QUERY_VECTOR = [0.125, 0.25, 0.5]
DOCUMENT_VECTORS = [[0.125, 0.25, 0.5], [0.75, 0.5, 0.25]]


@dataclass(frozen=True, slots=True)
class LlmCase:
    provider: str
    fixture: str
    base_url_environment: str
    base_url_suffix: str
    model: str
    expected_path: str


@dataclass(frozen=True, slots=True)
class CapturedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: object


def _capturing_fixture_server(
    fixture: Path,
    *,
    ledger: ExternalCallLedger,
    network_guard: NetworkDenyGuard,
) -> tuple[ProtocolFixtureServer, list[CapturedRequest]]:
    server = ProtocolFixtureServer(
        fixture,
        ledger=ledger,
        network_guard=network_guard,
    )
    captured: list[CapturedRequest] = []
    original_handler = server._handle  # type: ignore[attr-defined]

    def capture(handler: BaseHTTPRequestHandler) -> None:
        content_length = int(handler.headers.get("content-length", "0"))
        raw_body = handler.rfile.read(content_length)
        try:
            body: object = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            body = raw_body.decode("utf-8")
        captured.append(
            CapturedRequest(
                method=handler.command,
                path=handler.path,
                headers={
                    name.lower(): value for name, value in handler.headers.items()
                },
                body=body,
            )
        )
        original_handler(handler)

    server._handle = capture  # type: ignore[method-assign]
    return server, captured


def _scripted_llm_fake(
    ledger: ExternalCallLedger,
) -> tuple[ScriptedLlmFake, ScriptedScenario]:
    scenario = ScriptedScenario(
        "production-llm-parity-v1",
        (
            ScenarioStep(
                operation="llm.invoke",
                call=1,
                kind="response",
                payload=AIMessage(content=LLM_CONTENT, usage_metadata=LLM_USAGE),
                usage=LLM_USAGE,
            ),
        ),
    )
    return ScriptedLlmFake(scenario=scenario, ledger=ledger), scenario


def _assert_llm_contract(adapter: Any) -> None:
    response = adapter.invoke(LLM_PROMPT)
    assert isinstance(response, AIMessage)
    assert response.content == LLM_CONTENT
    assert response.usage_metadata is not None
    assert {
        key: response.usage_metadata[key]
        for key in ("input_tokens", "output_tokens", "total_tokens")
    } == LLM_USAGE


def _exercise_llm_and_fake(
    production: Any,
    *,
    ledger: ExternalCallLedger,
) -> None:
    fake, scenario = _scripted_llm_fake(ledger)
    with preserve_primary_error(lambda: _close_llm(production)):
        _assert_llm_contract(production)
        _assert_llm_contract(fake)
        scenario.assert_consumed()


@pytest.mark.parametrize(
    "case",
    (
        LlmCase(
            provider="openai",
            fixture="openai-chat-v1.json",
            base_url_environment="OPENAI_BASE_URL",
            base_url_suffix="/v1",
            model="gpt-offline-contract",
            expected_path="/v1/chat/completions",
        ),
        LlmCase(
            provider="anthropic",
            fixture="anthropic-messages-v1.json",
            base_url_environment="ANTHROPIC_BASE_URL",
            base_url_suffix="",
            model="claude-offline-contract",
            expected_path="/v1/messages",
        ),
        LlmCase(
            provider="google",
            fixture="google-gemini-v1.json",
            base_url_environment="GOOGLE_GEMINI_BASE_URL",
            base_url_suffix="",
            model="gemini-offline-contract",
            expected_path="/v1beta/models/gemini-offline-contract:generateContent",
        ),
    ),
    ids=lambda case: case.provider,
)
def test_endpoint_injectable_llm_factory_adapters_share_the_fake_contract(
    case: LlmCase,
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, captured = _capturing_fixture_server(
        FIXTURES / case.fixture,
        ledger=adapter_harness.ledger,
        network_guard=adapter_harness.network,
    )
    server.start()
    with preserve_primary_error(server.stop):
        monkeypatch.setenv(
            case.base_url_environment,
            f"{server.base_url}{case.base_url_suffix}",
        )
        production = LLMFactory.create_llm(
            ai_model=case.model,
            ai_provider=case.provider,
            ai_api_key="test",
            temperature=0.0,
        )

        _exercise_llm_and_fake(production, ledger=adapter_harness.ledger)

        assert server.calls == (ProtocolCall("POST", case.expected_path),)
        _assert_factory_request_schema(case, captured)


def _assert_factory_request_schema(
    case: LlmCase,
    captured: list[CapturedRequest],
) -> None:
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert request.path == case.expected_path
    assert request.headers["content-type"].startswith("application/json")
    assert isinstance(request.body, dict)
    if case.provider != "google":
        assert request.body["model"] == case.model
    if case.provider == "openai":
        assert request.headers["authorization"] == "Bearer test"
        assert request.headers["x-stainless-lang"] == "python"
        assert request.body["messages"] == [{"content": LLM_PROMPT, "role": "user"}]
        assert request.body["stream"] is False
        assert request.body["temperature"] == 0.0
        assert request.body["parallel_tool_calls"] is False
    elif case.provider == "anthropic":
        assert request.headers["x-api-key"] == "test"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.body["messages"] == [{"role": "user", "content": LLM_PROMPT}]
        assert request.body["temperature"] == 0.0
        assert request.body["max_tokens"] == 4096
    else:
        assert request.headers["x-goog-api-key"] == "test"
        assert "gl-python/" in request.headers["x-goog-api-client"]
        assert request.body["contents"] == [
            {"parts": [{"text": LLM_PROMPT}], "role": "user"}
        ]
        assert request.body["generationConfig"] == {
            "temperature": 0.0,
            "candidateCount": 1,
            "thinkingConfig": {"thinking_budget": 0},
        }


def test_openai_compatible_factory_adapter_shares_the_fake_contract(
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssrf_safe_transport, "_ALLOW_PRIVATE", True)
    server, captured = _capturing_fixture_server(
        FIXTURES / "openai-chat-v1.json",
        ledger=adapter_harness.ledger,
        network_guard=adapter_harness.network,
    )
    server.start()
    with preserve_primary_error(server.stop):
        production = LLMFactory.create_llm(
            ai_model="openai-compatible-offline-contract",
            ai_provider="openai_compatible",
            ai_api_key="test",
            ai_base_url=server.base_url,
            temperature=0.0,
            ai_custom_parameters={"max_retries": 0},
        )

        _exercise_llm_and_fake(production, ledger=adapter_harness.ledger)

        assert server.calls == (ProtocolCall("POST", "/v1/chat/completions"),)
        _assert_openai_chat_request(
            captured,
            model="openai-compatible-offline-contract",
            parallel_tool_calls=False,
        )


def test_cloudflare_factory_adapter_shares_the_fake_contract(
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[CapturedRequest] = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(
            CapturedRequest(
                method=request.method,
                path=request.url.path,
                headers={
                    name.lower(): value for name, value in request.headers.items()
                },
                body=json.loads(request.content),
            )
        )
        adapter_harness.ledger.record(
            boundary="llm",
            operation="post",
            outcome="status_200",
            phase="SIMULATED",
            target="gateway.ai.cloudflare.com:443",
            simulated=True,
        )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-cloudflare-offline-contract",
                "object": "chat.completion",
                "created": 1,
                "model": "cloudflare-offline-contract",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": LLM_CONTENT},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            },
            request=request,
        )

    sync_http = httpx.Client(transport=httpx.MockTransport(respond))
    async_http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
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
    production = LLMFactory.create_llm(
        ai_model="cloudflare-offline-contract",
        ai_provider="openai_compatible",
        ai_api_key="test",
        ai_base_url="https://gateway.ai.cloudflare.com/v1/offline/default/compat",
        temperature=0.0,
        ai_custom_parameters={"max_retries": 0},
    )
    assert isinstance(production, ChatCloudflareOpenAI)

    _exercise_llm_and_fake(production, ledger=adapter_harness.ledger)

    _assert_openai_chat_request(
        captured,
        model="cloudflare-offline-contract",
        expected_path="/v1/offline/default/compat/chat/completions",
        parallel_tool_calls=None,
    )


def _assert_openai_chat_request(
    captured: list[CapturedRequest],
    *,
    model: str,
    expected_path: str = "/v1/chat/completions",
    parallel_tool_calls: bool | None = False,
) -> None:
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert request.path == expected_path
    assert request.headers["content-type"].startswith("application/json")
    assert request.headers["authorization"] == "Bearer test"
    assert request.headers["x-stainless-lang"] == "python"
    assert isinstance(request.body, dict)
    assert request.body["model"] == model
    assert request.body["messages"] == [{"content": LLM_PROMPT, "role": "user"}]
    assert request.body["stream"] is False
    assert request.body["temperature"] == 0.0
    if parallel_tool_calls is None:
        assert "parallel_tool_calls" not in request.body
    else:
        assert request.body["parallel_tool_calls"] is parallel_tool_calls


def test_openrouter_factory_adapter_shares_the_fake_contract(
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_requests: list[tuple[str, str]] = []
    observed_payloads: list[object] = []
    observed_headers: list[dict[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        observed_requests.append((request.method, str(request.url)))
        observed_payloads.append(json.loads(request.content))
        observed_headers.append(
            {name.lower(): value for name, value in request.headers.items()}
        )
        adapter_harness.ledger.record(
            boundary="llm",
            operation="post",
            outcome="status_200",
            phase="SIMULATED",
            target="openrouter.mock:443",
            simulated=True,
        )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-openrouter-offline-contract",
                "object": "chat.completion",
                "created": 1,
                "model": "openrouter-offline-contract",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": LLM_CONTENT},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                },
            },
            request=request,
        )

    sync_http = httpx.Client(transport=httpx.MockTransport(respond))
    async_http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    original_init = ChatOpenRouter.__init__

    def init_with_in_process_transport(
        self: ChatOpenRouter,
        api_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.update(
            http_client=sync_http,
            http_async_client=async_http,
            max_retries=0,
        )
        original_init(self, api_key=api_key, **kwargs)

    monkeypatch.setattr(ChatOpenRouter, "__init__", init_with_in_process_transport)
    production = LLMFactory.create_llm(
        ai_model="openrouter-offline-contract",
        ai_provider="openrouter",
        ai_api_key="test",
        temperature=0.0,
    )
    assert isinstance(production, ChatOpenRouter)

    _exercise_llm_and_fake(production, ledger=adapter_harness.ledger)

    assert observed_requests == [
        ("POST", "https://openrouter.ai/api/v1/chat/completions")
    ]
    assert observed_payloads == [
        {
            "messages": [{"content": LLM_PROMPT, "role": "user"}],
            "model": "openrouter-offline-contract",
            "parallel_tool_calls": False,
            "stream": False,
            "temperature": 0.0,
        }
    ]
    assert observed_headers[0]["http-referer"] == "https://codecrow.cloud"
    assert observed_headers[0]["x-title"] == "CodeCrow AI"
    assert observed_headers[0]["authorization"] == "Bearer test"
    assert observed_headers[0]["x-stainless-lang"] == "python"


def test_vertex_factory_adapter_shares_the_fake_contract_without_live_auth(
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google.auth.credentials import AnonymousCredentials
    from google.oauth2 import service_account

    def deterministic_credentials(
        cls: type,
        info: dict[str, Any],
        scopes: list[str] | None = None,
        **kwargs: Any,
    ) -> AnonymousCredentials:
        credentials = AnonymousCredentials()
        credentials.token = "offline-test-access-token"
        return credentials

    monkeypatch.setattr(
        service_account.Credentials,
        "from_service_account_info",
        classmethod(deterministic_credentials),
    )
    server, captured = _capturing_fixture_server(
        FIXTURES / "google-vertex-v1.json",
        ledger=adapter_harness.ledger,
        network_guard=adapter_harness.network,
    )
    server.start()
    with preserve_primary_error(server.stop):
        monkeypatch.setenv("GOOGLE_VERTEX_BASE_URL", server.base_url)
        production = LLMFactory.create_llm(
            ai_model="gemini-offline-contract",
            ai_provider="google_vertex",
            ai_api_key=json.dumps(
                {
                    "type": "service_account",
                    "project_id": "offline-project",
                },
                sort_keys=True,
            ),
            ai_base_url="offline-project/global",
            temperature=0.0,
        )

        _exercise_llm_and_fake(production, ledger=adapter_harness.ledger)

        assert server.calls == (
            ProtocolCall(
                "POST",
                "/v1beta1/projects/offline-project/locations/global/"
                "publishers/google/models/gemini-offline-contract:generateContent",
            ),
        )
        assert len(captured) == 1
        model_request = captured[0]
        assert model_request.method == "POST"
        assert model_request.headers["authorization"] == (
            "Bearer offline-test-access-token"
        )
        assert "gl-python/" in model_request.headers["x-goog-api-client"]
        assert model_request.path.endswith(
            "/publishers/google/models/gemini-offline-contract:generateContent"
        )
        assert isinstance(model_request.body, dict)
        assert model_request.body["contents"] == [
            {"parts": [{"text": LLM_PROMPT}], "role": "user"}
        ]
        assert model_request.body["generationConfig"] == {
            "temperature": 0.0,
            "candidateCount": 1,
        }

def _scripted_embedding_fake(
    ledger: ExternalCallLedger,
) -> tuple[ScriptedEmbeddingFake, ScriptedScenario]:
    scenario = ScriptedScenario(
        "production-embedding-parity-v1",
        (
            ScenarioStep(
                operation="embedding.query",
                call=1,
                kind="response",
                payload=QUERY_VECTOR,
            ),
            ScenarioStep(
                operation="embedding.batch",
                call=1,
                kind="response",
                payload=DOCUMENT_VECTORS,
            ),
        ),
    )
    return (
        ScriptedEmbeddingFake(scenario=scenario, ledger=ledger, dimension=3),
        scenario,
    )


def _assert_embedding_contract(adapter: Any) -> None:
    _assert_embedding_query_contract(adapter)
    _assert_embedding_batch_contract(adapter)


def _assert_embedding_query_contract(adapter: Any) -> None:
    assert adapter.get_query_embedding("query") == QUERY_VECTOR


def _assert_embedding_batch_contract(adapter: Any) -> None:
    assert adapter.get_text_embedding_batch(["first", "second"]) == DOCUMENT_VECTORS


def _exercise_embedding_and_fake(
    production: Any,
    *,
    ledger: ExternalCallLedger,
) -> None:
    fake, scenario = _scripted_embedding_fake(ledger)
    with preserve_primary_error(lambda: close_adapter_clients(production)):
        _assert_embedding_contract(production)
        _assert_embedding_contract(fake)
        scenario.assert_consumed()


def test_openrouter_embedding_adapter_shares_the_fake_contract(
    adapter_harness: Any,
) -> None:
    server, captured = _capturing_fixture_server(
        FIXTURES / "openrouter-embedding-v1.json",
        ledger=adapter_harness.ledger,
        network_guard=adapter_harness.network,
    )
    server.start()
    production = OpenRouterEmbedding(
        api_key="test",
        model="openai/offline-embedding",
        api_base=f"{server.base_url}/v1",
        timeout=2.0,
        max_retries=0,
        embed_batch_size=8,
        expected_dim=3,
    )
    scenario = ScriptedScenario(
        "openrouter-embedding-query-parity",
        (
            ScenarioStep(
                "embedding.query",
                1,
                "response",
                payload=QUERY_VECTOR,
            ),
        ),
    )
    fake = ScriptedEmbeddingFake(
        scenario=scenario,
        ledger=adapter_harness.ledger,
        model="openai/offline-embedding",
        dimension=3,
    )
    with preserve_primary_error(
        lambda: close_adapter_clients(production),
        server.stop,
    ):
        _assert_embedding_query_contract(production)
        _assert_embedding_query_contract(fake)
        scenario.assert_consumed()
        assert server.calls == (ProtocolCall("POST", "/v1/embeddings"),)
        assert [request.body for request in captured] == [
            {
                "input": "query",
                "model": "openai/offline-embedding",
                "encoding_format": "base64",
            }
        ]
        assert captured[0].headers["authorization"] == "Bearer test"
        assert captured[0].headers["x-stainless-lang"] == "python"


def test_openrouter_embedding_batch_cardinality_shares_the_fake_contract(
    adapter_harness: Any,
) -> None:
    server, captured = _capturing_fixture_server(
        FIXTURES / "openrouter-embedding-batch-v1.json",
        ledger=adapter_harness.ledger,
        network_guard=adapter_harness.network,
    )
    server.start()
    production = OpenRouterEmbedding(
        api_key="test",
        model="openai/offline-embedding",
        api_base=f"{server.base_url}/v1",
        timeout=2.0,
        max_retries=0,
        embed_batch_size=8,
        expected_dim=3,
    )
    scenario = ScriptedScenario(
        "openrouter-embedding-batch-parity",
        (
            ScenarioStep(
                "embedding.batch",
                1,
                "response",
                payload=DOCUMENT_VECTORS,
            ),
        ),
    )
    fake = ScriptedEmbeddingFake(
        scenario=scenario,
        ledger=adapter_harness.ledger,
        model="openai/offline-embedding",
        dimension=3,
    )
    with preserve_primary_error(
        lambda: close_adapter_clients(production),
        server.stop,
    ):
        _assert_embedding_batch_contract(production)
        _assert_embedding_batch_contract(fake)
        scenario.assert_consumed()
        assert server.calls == (ProtocolCall("POST", "/v1/embeddings"),)
        assert [request.body for request in captured] == [
            {
                "input": ["first", "second"],
                "model": "openai/offline-embedding",
                "encoding_format": "base64",
            }
        ]
        assert captured[0].headers["authorization"] == "Bearer test"
        assert captured[0].headers["x-stainless-lang"] == "python"


def test_ollama_embedding_adapter_shares_the_fake_contract(
    adapter_harness: Any,
) -> None:
    server, captured = _capturing_fixture_server(
        FIXTURES / "ollama-embedding-v1.json",
        ledger=adapter_harness.ledger,
        network_guard=adapter_harness.network,
    )
    server.start()
    with preserve_primary_error(server.stop):
        production = OllamaEmbedding(
            model="offline-embedding",
            base_url=server.base_url,
            timeout=2.0,
            embed_batch_size=8,
            expected_dim=3,
            max_retries=0,
            retry_base_delay=0.0,
        )

        _exercise_embedding_and_fake(production, ledger=adapter_harness.ledger)

        assert server.calls == (
            ProtocolCall("GET", "/api/tags"),
            ProtocolCall("POST", "/api/embeddings"),
            ProtocolCall("POST", "/api/embed"),
        )
        assert [request.body for request in captured] == [
            None,
            {"model": "offline-embedding", "prompt": "query"},
            {"model": "offline-embedding", "input": ["first", "second"]},
        ]
        assert all("authorization" not in request.headers for request in captured)


def test_unregistered_production_adapter_endpoint_is_denied_and_acknowledged(
    adapter_harness: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssrf_safe_transport, "_ALLOW_PRIVATE", True)
    production = LLMFactory.create_llm(
        ai_model="unregistered-offline-contract",
        ai_provider="openai_compatible",
        ai_api_key="test",
        ai_base_url="http://127.0.0.1:9",
        temperature=0.0,
        ai_custom_parameters={"max_retries": 0},
    )
    with preserve_primary_error(lambda: _close_llm(production)):
        with pytest.raises(Exception) as raised:
            production.invoke("This call must never reach a socket.")

        denial = _find_denial(raised.value)
        assert denial is not None
        assert denial.call is not None
        assert denial.call.boundary == "network"
        assert denial.call.operation == "connect"
        assert denial.call.outcome == "blocked"
        assert denial.call.phase == "PRE_DNS"
        assert denial.call.target == "127.0.0.1:9"
        adapter_harness.ledger.acknowledge_blocked(
            denial.call,
            boundary="network",
            operation="connect",
            phase="PRE_DNS",
            target="127.0.0.1:9",
        )


def _find_denial(error: BaseException) -> UnexpectedExternalCall | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, UnexpectedExternalCall):
            return current
        current = current.__cause__ or current.__context__
    return None


def _close_llm(model: Any) -> None:
    close_adapter_clients(model)
