"""Security and provenance contracts for the agentic repository/RAG tools."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from model.dtos import ExecutionManifestV1, RagExecutionConfigV1, ReviewRequestDto
from service.review.agentic.tool_gateway import (
    AgenticToolError,
    AgenticToolGateway,
    ToolGatewayLimits,
)
from service.review.telemetry import StageOutcome, bind_telemetry, reset_telemetry
from utils.diff_processor import DiffProcessor


BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
MERGE_BASE_SHA = "c" * 40
EXECUTION_ID = "agentic-execution-1"
QUOTED_RENAME_DIFF = r'''diff --git "a/old folder/na\303\257ve.py" "b/new folder/\344\275\240\345\245\275.py"
similarity index 80%
rename from "old folder/na\303\257ve.py"
rename to "new folder/\344\275\240\345\245\275.py"
--- "a/old folder/na\303\257ve.py"
+++ "b/new folder/\344\275\240\345\245\275.py"
@@ -1 +1,2 @@
-old = 1
+new = 1
+validate(new)
'''


def _request(
    raw_diff: str,
    *,
    changed_files: list[str] | None = None,
    index_version: str | None = None,
):
    selected_index = index_version or f"rag-commit-{BASE_SHA}"
    manifest = ExecutionManifestV1.model_construct(
        executionId=EXECUTION_ID,
        baseSha=BASE_SHA,
        headSha=HEAD_SHA,
        mergeBaseSha=MERGE_BASE_SHA,
        diffDigest=sha256(raw_diff.encode("utf-8")).hexdigest(),
        pullRequestId=17,
    )
    return ReviewRequestDto.model_construct(
        executionManifest=manifest,
        projectVcsWorkspace="acme",
        projectVcsRepoSlug="payments",
        projectWorkspace="ignored-legacy-workspace",
        projectNamespace="ignored-legacy-project",
        sourceBranchName="feature/payments",
        targetBranchName="main",
        pullRequestId=17,
        changedFiles=changed_files or ["src/payments.py"],
        rawDiff=raw_diff,
        indexVersion=selected_index,
        ragContext=RagExecutionConfigV1(
            schemaVersion=1,
            indexVersion=selected_index,
            parserVersion="tree-sitter-v1",
            chunkerVersion="ast-code-splitter-v1",
            embeddingVersion="configured-v1",
        ),
        reviewApproach="AGENTIC",
    )


@pytest.fixture
def raw_diff() -> str:
    return """diff --git a/src/payments.py b/src/payments.py
index 1111111..2222222 100644
--- a/src/payments.py
+++ b/src/payments.py
@@ -1,3 +1,4 @@
 def charge(token):
+    validate(token)
     return gateway.charge(token)
 
diff --git a/src/other.py b/src/other.py
--- a/src/other.py
+++ b/src/other.py
@@ -10,2 +10,2 @@
-old = True
+new = True
 context = 1
"""


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src" / "payments.py").write_text(
        "def charge(token):\n"
        "    api_key = 'super-secret-value'\n"
        "    validate(token)\n"
        "    return gateway.charge(token)\n",
        encoding="utf-8",
    )
    (source / "src" / "other.py").write_text(
        "class ChargeService:\n"
        "    def charge(self, token):\n"
        "        return token\n",
        encoding="utf-8",
    )
    (source / ".env").write_text("TOKEN=never-return-this\n", encoding="utf-8")
    return source


def _gateway(
    repository: Path,
    raw_diff: str,
    *,
    rag_client=None,
    limits: ToolGatewayLimits | None = None,
    index_version: str | None = None,
) -> AgenticToolGateway:
    return AgenticToolGateway(
        workspace_root=repository,
        request=_request(raw_diff, index_version=index_version),
        rag_client=rag_client,
        processed_diff=DiffProcessor().process(raw_diff),
        limits=limits,
    )


def _rag_chunk(
    text: str,
    *,
    path: str,
    branch: str,
    revision: str,
    execution_id: str | None = None,
    pr: bool = False,
    match_type: str | None = None,
) -> dict:
    metadata = {
        "path": path,
        "branch": branch,
        "snapshot_sha": revision,
        "content_digest": sha256(text.encode("utf-8")).hexdigest(),
        "parser_version": "tree-sitter-v1",
        "chunker_version": "ast-code-splitter-v1",
        "embedding_version": "configured-v1",
        "start_line": 1,
        "end_line": max(1, text.count("\n") + 1),
    }
    if pr:
        metadata.update({"pr": True, "execution_id": execution_id})
    value = {"text": text, "score": 0.94, "metadata": metadata}
    if match_type:
        value.update({"_source": "deterministic", "_match_type": match_type})
    return value


def test_exposes_only_the_bounded_review_tool_set(repository: Path, raw_diff: str):
    gateway = _gateway(repository, raw_diff)

    definitions = gateway.tool_definitions()

    assert {item["name"] for item in definitions} == {
        "list_files",
        "search_text",
        "read_file",
        "find_symbol",
        "read_diff_hunk",
        "rag_search",
        "rag_related",
        "rag_similar_code",
    }
    serialized = repr(definitions).lower()
    assert "shell" not in serialized
    assert "command" not in serialized
    assert "workspace" not in {
        property_name
        for definition in definitions
        for property_name in definition["inputSchema"].get("properties", {})
    }


@pytest.mark.asyncio
async def test_local_tools_are_read_only_bounded_and_emit_exact_receipts(
    repository: Path, raw_diff: str
):
    gateway = _gateway(repository, raw_diff)

    listed = await gateway.invoke("list_files", {"pattern": "src/*.py"})
    searched = await gateway.invoke(
        "search_text", {"query": "gateway.charge", "path_pattern": "src/*.py"}
    )
    symbol = await gateway.invoke("find_symbol", {"name": "ChargeService"})
    read = await gateway.invoke(
        "read_file", {"path": "src/payments.py", "start_line": 1, "end_line": 4}
    )

    assert [item["path"] for item in listed["results"]] == [
        "src/other.py",
        "src/payments.py",
    ]
    assert searched["results"][0]["line"] == 4
    assert searched["results"][0]["proof_required"] is True
    assert symbol["results"][0]["path"] == "src/other.py"
    assert "super-secret-value" not in read["content"]
    assert "[REDACTED]" in read["content"]
    expected_proof = {
        "kind": "exact_source_span_v1",
        "execution_id": EXECUTION_ID,
        "head_sha": HEAD_SHA,
        "snapshot_id": gateway.snapshot_id,
        "path": "src/payments.py",
        "start_line": 1,
        "end_line": 4,
        "source_digest": sha256(
            (repository / "src/payments.py").read_bytes()
        ).hexdigest(),
        "span_digest": sha256(
            (repository / "src/payments.py").read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest(),
    }
    observed_proof = dict(read["proof"])
    receipt_id = observed_proof.pop("receipt_id")
    assert observed_proof == expected_proof
    assert len(receipt_id) == 64
    assert gateway.validate_proof(
        read["proof"], expected_path="src/payments.py"
    ) is True
    assert read["redacted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, arguments",
    [
        ("read_file", {"path": "../outside.py"}),
        ("read_file", {"path": "/etc/passwd"}),
        ("list_files", {"pattern": "../*"}),
        ("search_text", {"query": "x", "path_pattern": "/etc/*"}),
    ],
)
async def test_path_escape_is_rejected(
    repository: Path, raw_diff: str, tool: str, arguments: dict
):
    gateway = _gateway(repository, raw_diff)

    with pytest.raises(AgenticToolError, match="path") as error:
        await gateway.invoke(tool, arguments)

    assert error.value.code == "INVALID_PATH"


@pytest.mark.asyncio
async def test_symlinks_and_secret_stores_are_not_readable(repository: Path, raw_diff: str):
    outside = repository.parent / "outside.py"
    outside.write_text("outside secret", encoding="utf-8")
    (repository / "src" / "escape.py").symlink_to(outside)
    (repository / ".aws").mkdir()
    (repository / ".aws" / "credentials").write_text(
        "aws_secret_access_key=never-return-this", encoding="utf-8"
    )
    gateway = _gateway(repository, raw_diff)

    for path in ("src/escape.py", ".env", ".aws/credentials"):
        with pytest.raises(AgenticToolError) as error:
            await gateway.invoke("read_file", {"path": path})
        assert error.value.code in {"INVALID_PATH", "SENSITIVE_PATH"}

    listed = await gateway.invoke("list_files", {"pattern": "*"})
    assert ".env" not in {item["path"] for item in listed["results"]}
    assert "src/escape.py" not in {item["path"] for item in listed["results"]}
    assert ".aws/credentials" not in {item["path"] for item in listed["results"]}


@pytest.mark.asyncio
async def test_common_secret_shapes_are_redacted_from_reads_and_search(
    repository: Path, raw_diff: str
):
    aws_secret = "aws-secret-material"
    database_password = "database-password"
    github_token = "ghp_" + ("a" * 36)
    secret_file = repository / "src" / "secret_shapes.py"
    secret_file.write_text(
        f'AWS_SECRET_ACCESS_KEY = "{aws_secret}"\n'
        f'DATABASE_URL = "postgresql://service:{database_password}@db/app"\n'
        f'public_example = "{github_token}"\n',
        encoding="utf-8",
    )
    gateway = _gateway(repository, raw_diff)

    read = await gateway.invoke(
        "read_file",
        {"path": "src/secret_shapes.py", "start_line": 1, "end_line": 3},
    )
    searched = await gateway.invoke(
        "search_text",
        {"query": database_password, "path_pattern": "src/secret_shapes.py"},
    )
    serialized = repr({"read": read, "searched": searched})

    assert aws_secret not in serialized
    assert database_password not in serialized
    assert github_token not in serialized
    assert "[REDACTED" in serialized


@pytest.mark.asyncio
async def test_unknown_arguments_cannot_override_execution_coordinates(
    repository: Path, raw_diff: str
):
    rag = SimpleNamespace(semantic_search=AsyncMock())
    gateway = _gateway(repository, raw_diff, rag_client=rag)

    with pytest.raises(AgenticToolError) as error:
        await gateway.invoke(
            "rag_search",
            {"query": "charge", "workspace": "attacker", "revision": "deadbeef"},
        )

    assert error.value.code == "INVALID_ARGUMENTS"
    rag.semantic_search.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_and_output_budgets_are_shared_across_local_and_rag_tools(
    repository: Path, raw_diff: str
):
    limits = ToolGatewayLimits(
        max_calls=2,
        max_results=10,
        max_output_bytes_per_call=600,
        max_total_output_bytes=360,
    )
    gateway = _gateway(repository, raw_diff, limits=limits)

    first = await gateway.invoke("list_files", {"pattern": "*"})
    second = await gateway.invoke("search_text", {"query": "charge"})

    assert len(repr(first).encode("utf-8")) < 1200
    assert second["truncated"] is True
    with pytest.raises(AgenticToolError) as error:
        await gateway.invoke("list_files", {})
    assert error.value.code == "CALL_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_literal_search_does_not_interpret_regular_expressions(
    repository: Path, raw_diff: str
):
    (repository / "src" / "literal.py").write_text("value = 'a.*b'\naZZb\n")
    gateway = _gateway(repository, raw_diff)

    result = await gateway.invoke("search_text", {"query": "a.*b"})

    assert [(item["path"], item["line"]) for item in result["results"]] == [
        ("src/literal.py", 1)
    ]


@pytest.mark.asyncio
async def test_search_scans_beyond_the_result_limit_of_file_listing(
    repository: Path, raw_diff: str
):
    for index in range(5):
        (repository / "src" / f"a{index}.py").write_text("nothing here\n")
    (repository / "src" / "z-last.py").write_text("needle is here\n")
    gateway = _gateway(
        repository,
        raw_diff,
        limits=ToolGatewayLimits(max_results=2),
    )

    result = await gateway.invoke("search_text", {"query": "needle"})

    assert [item["path"] for item in result["results"]] == ["src/z-last.py"]


@pytest.mark.asyncio
async def test_empty_source_has_no_fabricated_line_receipt(repository: Path, raw_diff: str):
    (repository / "src" / "empty.py").write_bytes(b"")
    gateway = _gateway(repository, raw_diff)

    with pytest.raises(AgenticToolError) as error:
        await gateway.invoke("read_file", {"path": "src/empty.py", "start_line": 1})

    assert error.value.code == "LINE_NOT_FOUND"


@pytest.mark.asyncio
async def test_php_namespaced_symbol_is_searchable(repository: Path, raw_diff: str):
    (repository / "src" / "service.php").write_text(
        "<?php $service = Acme\\Payments\\ChargeService::class;\n",
        encoding="utf-8",
    )
    gateway = _gateway(repository, raw_diff)

    result = await gateway.invoke(
        "find_symbol", {"name": "Acme\\Payments\\ChargeService"}
    )

    assert result["results"][0]["path"] == "src/service.php"


@pytest.mark.asyncio
async def test_read_diff_hunk_uses_the_immutable_raw_diff(
    repository: Path, raw_diff: str
):
    gateway = _gateway(repository, raw_diff)

    result = await gateway.invoke(
        "read_diff_hunk", {"path": "src/payments.py", "line": 2}
    )

    assert result["new_start"] == 1
    assert result["new_count"] == 4
    assert "+    validate(token)" in result["content"]
    assert result["proof"]["kind"] == "exact_diff_hunk_v1"
    assert result["proof"]["diff_digest"] == sha256(
        raw_diff.encode("utf-8")
    ).hexdigest()
    assert result["proof"]["head_sha"] == HEAD_SHA
    assert gateway.validate_proof(result["proof"], expected_path="src/payments.py")


@pytest.mark.asyncio
async def test_read_diff_hunk_decodes_java_accepted_quoted_utf8_rename_path(
    tmp_path: Path,
):
    repository = tmp_path / "quoted-repository"
    renamed = repository / "new folder" / "你好.py"
    renamed.parent.mkdir(parents=True)
    renamed.write_text("new = 1\nvalidate(new)\n", encoding="utf-8")
    gateway = AgenticToolGateway(
        workspace_root=repository,
        request=_request(
            QUOTED_RENAME_DIFF,
            changed_files=["new folder/你好.py"],
        ),
        rag_client=None,
        processed_diff=DiffProcessor().process(QUOTED_RENAME_DIFF),
    )

    result = await gateway.invoke(
        "read_diff_hunk",
        {"path": "new folder/你好.py", "line": 2},
    )

    assert result["path"] == "new folder/你好.py"
    assert result["new_start"] == 1
    assert result["new_count"] == 2
    assert "+validate(new)" in result["content"]
    assert gateway.validate_proof(
        result["proof"], expected_path="new folder/你好.py"
    )


@pytest.mark.asyncio
async def test_proof_validation_rejects_tampering_wrong_path_and_workspace_changes(
    repository: Path, raw_diff: str
):
    gateway = _gateway(repository, raw_diff)
    result = await gateway.invoke(
        "read_file", {"path": "src/payments.py", "start_line": 1, "end_line": 2}
    )
    proof = result["proof"]

    tampered = dict(proof, span_digest="0" * 64)
    assert gateway.validate_proof(tampered) is False
    assert gateway.validate_proof(proof, expected_path="src/other.py") is False

    (repository / "src/payments.py").write_text("changed after receipt\n", encoding="utf-8")
    assert gateway.validate_proof(proof) is False


def test_function_definitions_and_receipts_are_engine_ready(repository: Path, raw_diff: str):
    gateway = _gateway(repository, raw_diff)

    definitions = gateway.langchain_tool_definitions()

    assert all(item["type"] == "function" for item in definitions)
    assert definitions[0]["function"]["name"] == "list_files"
    assert definitions[0]["function"]["parameters"]["additionalProperties"] is False
    assert gateway.receipts == []
    assert gateway.telemetry_summary["execution_id"] == EXECUTION_ID


@pytest.mark.asyncio
async def test_rag_search_injects_coordinates_and_rejects_foreign_receipts(
    repository: Path, raw_diff: str
):
    accepted = _rag_chunk(
        "def related(): pass",
        path="src/other.py",
        branch="main",
        revision=BASE_SHA,
    )
    foreign = _rag_chunk(
        "def poisoned(): pass",
        path="src/poisoned.py",
        branch="feature/payments",
        revision=HEAD_SHA,
        execution_id="foreign-execution",
        pr=True,
    )

    async def semantic_search(**kwargs):
        if kwargs["branch"] == "main":
            return {"results": [accepted]}
        return {"results": [foreign]}

    rag = SimpleNamespace(semantic_search=AsyncMock(side_effect=semantic_search))
    gateway = _gateway(repository, raw_diff, rag_client=rag)

    result = await gateway.invoke(
        "rag_search", {"query": "where is charging implemented?", "path_pattern": "src/*"}
    )

    assert [item["path"] for item in result["results"]] == ["src/other.py"]
    assert result["proof_status"] == "exact_source_read_required"
    assert result["results"][0]["proof_required"] is True
    assert result["rejected_result_count"] == 1
    assert result["snapshot_receipt"]["head_sha"] == HEAD_SHA
    assert rag.semantic_search.await_count == 2
    source_call, base_call = rag.semantic_search.await_args_list
    assert source_call.kwargs["workspace"] == "acme"
    assert source_call.kwargs["project"] == "payments"
    assert source_call.kwargs["branch"] == "feature/payments"
    assert source_call.kwargs["revision"] == HEAD_SHA
    assert base_call.kwargs["branch"] == "main"
    assert base_call.kwargs["revision"] == BASE_SHA
    for call in rag.semantic_search.await_args_list:
        assert call.kwargs["execution_id"] == EXECUTION_ID
        assert call.kwargs["snapshot"] == gateway.snapshot


@pytest.mark.asyncio
async def test_rag_related_uses_bound_pr_and_structural_query(
    repository: Path, raw_diff: str
):
    chunk = _rag_chunk(
        "class ChargeService: pass",
        path="src/other.py",
        branch="main",
        revision=BASE_SHA,
        match_type="definition",
    )
    rag = SimpleNamespace(
        get_deterministic_context=AsyncMock(
            return_value={
                "context": {
                    "chunks": [chunk],
                    "related_definitions": {"ChargeService": [chunk]},
                }
            }
        )
    )
    gateway = _gateway(repository, raw_diff, rag_client=rag)

    result = await gateway.invoke(
        "rag_related", {"path_or_symbol": "ChargeService"}
    )

    assert result["results"][0]["relationship_type"] == "definition"
    call = rag.get_deterministic_context.await_args.kwargs
    assert call["workspace"] == "acme"
    assert call["project"] == "payments"
    assert call["branches"] == ["feature/payments", "main"]
    assert call["file_paths"] == ["src/payments.py"]
    assert call["additional_identifiers"] == ["ChargeService"]
    assert call["pr_number"] == 17
    assert call["execution_id"] == EXECUTION_ID


@pytest.mark.asyncio
async def test_rag_similar_code_builds_query_from_exact_source_but_returns_leads(
    repository: Path, raw_diff: str
):
    duplicate = _rag_chunk(
        "def alternate_charge(token): return token",
        path="src/other.py",
        branch="main",
        revision=BASE_SHA,
    )
    duplicate["_source"] = "duplication"
    rag = SimpleNamespace(search_for_duplicates=AsyncMock(return_value=[duplicate]))
    gateway = _gateway(repository, raw_diff, rag_client=rag)

    result = await gateway.invoke(
        "rag_similar_code",
        {"path": "src/payments.py", "start_line": 1, "end_line": 4},
    )

    call = rag.search_for_duplicates.await_args.kwargs
    assert call["workspace"] == "acme"
    assert call["project"] == "payments"
    assert call["branch"] == "feature/payments"
    assert call["base_branch"] == "main"
    assert call["execution_id"] == EXECUTION_ID
    assert "super-secret-value" not in call["queries"][0]
    assert result["query_source_proof"]["kind"] == "exact_source_span_v1"
    assert result["results"][0]["proof_required"] is True


@pytest.mark.asyncio
async def test_missing_rag_is_an_explicit_context_gap(repository: Path, raw_diff: str):
    gateway = _gateway(repository, raw_diff, rag_client=None)

    result = await gateway.invoke("rag_search", {"query": "charge implementation"})

    assert result["results"] == []
    assert {gap["code"] for gap in result["gaps"]} >= {
        "rag_client_unavailable",
        "related_context_empty",
    }


@pytest.mark.asyncio
async def test_disabled_rag_tools_never_call_client_and_report_bound_gap(
    repository: Path, raw_diff: str
):
    rag = SimpleNamespace(
        semantic_search=AsyncMock(side_effect=AssertionError("RAG must stay disabled")),
        get_deterministic_context=AsyncMock(
            side_effect=AssertionError("RAG must stay disabled")
        ),
        search_for_duplicates=AsyncMock(
            side_effect=AssertionError("RAG must stay disabled")
        ),
    )
    gateway = _gateway(
        repository,
        raw_diff,
        rag_client=rag,
        index_version="rag-disabled",
    )

    calls = [
        ("rag_search", {"query": "charge", "path_pattern": "src/*"}),
        ("rag_related", {"path_or_symbol": "ChargeService"}),
        (
            "rag_similar_code",
            {"path": "src/payments.py", "start_line": 1, "end_line": 2},
        ),
    ]
    for tool_name, arguments in calls:
        result = await gateway.invoke(tool_name, arguments)
        assert any(gap["code"] == "rag_disabled" for gap in result["gaps"])

    rag.semantic_search.assert_not_awaited()
    rag.get_deterministic_context.assert_not_awaited()
    rag.search_for_duplicates.assert_not_awaited()


@pytest.mark.asyncio
async def test_rag_call_timeout_is_bounded(repository: Path, raw_diff: str):
    async def slow(**_kwargs):
        await asyncio.sleep(1)
        return {"results": []}

    rag = SimpleNamespace(semantic_search=AsyncMock(side_effect=slow))
    gateway = _gateway(
        repository,
        raw_diff,
        rag_client=rag,
        limits=ToolGatewayLimits(call_timeout_seconds=0.01),
    )

    with pytest.raises(AgenticToolError) as error:
        await gateway.invoke("rag_search", {"query": "charge implementation"})

    assert error.value.code == "TOOL_TIMEOUT"


@pytest.mark.asyncio
async def test_telemetry_never_records_queries_or_source(repository: Path, raw_diff: str):
    gateway = _gateway(repository, raw_diff)
    await gateway.invoke(
        "search_text", {"query": "super-secret-value", "path_pattern": "src/*"}
    )

    telemetry = gateway.telemetry_snapshot()
    serialized = repr(telemetry)

    assert telemetry["execution_id"] == EXECUTION_ID
    assert telemetry["local_calls"] == 1
    assert "super-secret-value" not in serialized
    assert "gateway.charge" not in serialized
    assert set(telemetry["events"][0]) == {
        "tool",
        "kind",
        "success",
        "duration_ms",
        "output_bytes",
        "error_code",
    }


@pytest.mark.asyncio
async def test_agentic_tools_record_content_free_terminal_telemetry(
    repository: Path, raw_diff: str
):
    gateway = _gateway(repository, raw_diff)
    recorder = MagicMock()
    token = bind_telemetry(recorder)
    try:
        await gateway.invoke(
            "search_text",
            {"query": "super-secret-value", "path_pattern": "src/*"},
        )
        with pytest.raises(AgenticToolError):
            await gateway.invoke(
                "read_file",
                {"path": "src/payments.py", "workspace": "attacker/repo"},
            )
    finally:
        reset_telemetry(token)

    calls = [call.args[0] for call in recorder.record_tool_call.call_args_list]
    assert [(call.stage, call.tool, call.outcome, call.reason) for call in calls] == [
        ("agentic_review", "search_text", StageOutcome.COMPLETE, None),
        (
            "agentic_review",
            "read_file",
            StageOutcome.FAILED,
            "invalid_arguments",
        ),
    ]
    serialized = repr(calls)
    assert "super-secret-value" not in serialized
    assert "attacker/repo" not in serialized
    assert "gateway.charge" not in serialized


@pytest.mark.asyncio
async def test_terminal_tool_telemetry_failure_is_observational(
    repository: Path, raw_diff: str
):
    gateway = _gateway(repository, raw_diff)
    recorder = MagicMock()
    recorder.record_tool_call.side_effect = RuntimeError("telemetry closed")
    token = bind_telemetry(recorder)
    try:
        result = await gateway.invoke("list_files", {"pattern": "src/*.py"})
    finally:
        reset_telemetry(token)

    assert [item["path"] for item in result["results"]] == [
        "src/other.py",
        "src/payments.py",
    ]
