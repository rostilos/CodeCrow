from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import Any

from .ledger import ExternalCallLedger
from .scenario import ScenarioContractError, ScriptedScenario, SimulatedResult


class ScriptedBoundaryFake:
    def __init__(
        self,
        *,
        boundary: str,
        target: str,
        scenario: ScriptedScenario,
        ledger: ExternalCallLedger,
    ) -> None:
        if not boundary or not target:
            raise ValueError("fake boundary and target must not be empty")
        self.boundary = boundary
        self.target = target
        self.scenario = scenario
        self.ledger = ledger
        self.last_usage: Mapping[str, int] = {}

    def call(self, operation: str) -> SimulatedResult:
        step = self.scenario.take(operation)
        try:
            result = step.resolve()
        except BaseException:
            self._record(operation, step.kind)
            raise
        self.last_usage = result.usage
        self._record(operation, step.kind)
        return result

    async def acall(self, operation: str) -> SimulatedResult:
        return self.call(operation)

    def _record(self, operation: str, outcome: str) -> None:
        self.ledger.record(
            boundary=self.boundary,
            operation=operation,
            outcome=outcome,
            phase="SIMULATED",
            target=self.target,
            simulated=True,
        )


class ScriptedLlmFake(ScriptedBoundaryFake):
    def __init__(
        self,
        *,
        scenario: ScriptedScenario,
        ledger: ExternalCallLedger,
        model: str = "test-model-v1",
    ) -> None:
        super().__init__(
            boundary="llm",
            target=f"fake-llm:{_stable_port(model)}",
            scenario=scenario,
            ledger=ledger,
        )
        self.model = model
        self.output_schema: object | None = None
        self.bound_options: dict[str, object] = {}
        self.bound_tools: tuple[object, ...] = ()
        self.invocations: list[object] = []

    def bind(self, **options: object) -> ScriptedLlmFake:
        self.bound_options = dict(options)
        return self

    def bind_tools(self, tools: Sequence[object], **options: object) -> ScriptedLlmFake:
        self.bound_tools = tuple(tools)
        self.bound_options = dict(options)
        return self

    def with_structured_output(self, schema: object, **_: object) -> ScriptedLlmFake:
        self.output_schema = schema
        return self

    def invoke(self, value: object, **__: object) -> Any:
        self.invocations.append(value)
        return self.call("llm.invoke").payload

    async def ainvoke(self, value: object, **__: object) -> Any:
        self.invocations.append(value)
        return (await self.acall("llm.ainvoke")).payload

    def stream(self, value: object, **__: object) -> Iterator[Any]:
        self.invocations.append(value)
        result = self.call("llm.stream")
        if result.kind != "stream":
            raise ScenarioContractError("LLM stream operation requires a stream scenario step")
        yield from result.chunks

    async def astream(self, value: object, **__: object) -> AsyncIterator[Any]:
        self.invocations.append(value)
        result = await self.acall("llm.astream")
        if result.kind != "stream":
            raise ScenarioContractError("LLM astream operation requires a stream scenario step")
        for chunk in result.chunks:
            yield chunk


class ScriptedEmbeddingFake(ScriptedBoundaryFake):
    def __init__(
        self,
        *,
        scenario: ScriptedScenario,
        ledger: ExternalCallLedger,
        model: str = "test-embedding-v1",
        dimension: int = 4,
    ) -> None:
        if dimension < 1:
            raise ValueError("embedding dimension must be positive")
        super().__init__(
            boundary="embedding",
            target=f"fake-embedding:{_stable_port(model)}",
            scenario=scenario,
            ledger=ledger,
        )
        self.model = model
        self.dimension = dimension

    def get_query_embedding(self, text: str) -> list[float]:
        _require_embedding_text(text)
        return self._vector("embedding.query")

    def get_text_embedding(self, text: str) -> list[float]:
        _require_embedding_text(text)
        return self._vector("embedding.text")

    def get_text_embedding_batch(self, texts: Sequence[str]) -> list[list[float]]:
        batch = tuple(texts)
        for text in batch:
            _require_embedding_text(text)
        if not batch:
            return []
        result = self.call("embedding.batch").payload
        vectors = [list(vector) for vector in result]
        if len(vectors) != len(batch):
            raise ScenarioContractError("embedding batch size does not match input size")
        for vector in vectors:
            self._validate_vector(vector)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.get_query_embedding(text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self.get_text_embedding_batch(texts)

    def _vector(self, operation: str) -> list[float]:
        vector = list(self.call(operation).payload)
        self._validate_vector(vector)
        return vector

    def _validate_vector(self, vector: Sequence[float]) -> None:
        if len(vector) != self.dimension:
            raise ScenarioContractError(
                f"expected embedding dimension {self.dimension}, received {len(vector)}"
            )


class ContentAddressedEmbeddingFake:
    def __init__(self, *, ledger: ExternalCallLedger, dimension: int = 4) -> None:
        if dimension < 1:
            raise ValueError("embedding dimension must be positive")
        self.ledger = ledger
        self.dimension = dimension
        self.model = "content-addressed-sha256-v1"

    def embed_query(self, text: str) -> list[float]:
        return self._embed("embedding.query", text)

    def get_query_embedding(self, text: str) -> list[float]:
        return self.embed_query(text)

    def get_text_embedding(self, text: str) -> list[float]:
        return self._embed("embedding.text", text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        batch = tuple(texts)
        for text in batch:
            _require_embedding_text(text)
        return [self._embed("embedding.document", text) for text in batch]

    def get_text_embedding_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    def _embed(self, operation: str, text: str) -> list[float]:
        _require_embedding_text(text)
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        repeats = (self.dimension + len(digest) - 1) // len(digest)
        vector = [round(byte / 255.0, 8) for byte in (digest * repeats)[: self.dimension]]
        self.ledger.record(
            boundary="embedding",
            operation=operation,
            outcome="response",
            phase="SIMULATED",
            target="content-addressed:443",
            simulated=True,
        )
        return vector


def _require_embedding_text(text: object) -> str:
    if not isinstance(text, str):
        raise TypeError("embedding text must be a string")
    if not text.strip():
        raise ValueError("cannot embed empty text")
    return text


def _stable_port(identity: str) -> int:
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    return 10000 + int.from_bytes(digest[:2], "big") % 50000
