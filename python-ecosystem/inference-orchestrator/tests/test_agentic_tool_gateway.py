"""Focused tests for the local-only agentic tool gateway."""

from pathlib import Path

import pytest

from model.dtos import ReviewRequestDto
from service.review.agentic.tool_gateway import (
    AgenticToolError,
    AgenticToolGateway,
    ToolGatewayLimits,
)


RAW_DIFF = """diff --git a/src/payments.py b/src/payments.py
--- a/src/payments.py
+++ b/src/payments.py
@@ -1,2 +1,3 @@
 def charge(token):
+    validate(token)
     return gateway.charge(token)
"""
HEAD_SHA = "b" * 40
WORKSPACE_KEY = "d" * 64


def _request(raw_diff: str = RAW_DIFF) -> ReviewRequestDto:
    return ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="acme",
        projectVcsRepoSlug="payments",
        projectWorkspace="acme",
        projectNamespace="payments",
        aiProvider="OPENAI",
        aiModel="test-model",
        aiApiKey="test-key",
        reviewApproach="AGENTIC",
        rawDiff=raw_diff,
        previousCommitHash="a" * 40,
        currentCommitHash=HEAD_SHA,
        agenticRepository={
            "workspaceKey": WORKSPACE_KEY,
            "snapshotSha": HEAD_SHA,
            "contentDigest": "e" * 64,
            "byteLength": 100,
        },
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "src").mkdir(parents=True)
    (root / "src" / "payments.py").write_text(
        "def charge(token):\n"
        "    api_key = 'super-secret-value'\n"
        "    validate(token)\n"
        "    return gateway.charge(token)\n",
        encoding="utf-8",
    )
    (root / "src" / "other.py").write_text("value = 1\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=must-not-leak\n", encoding="utf-8")
    return root


def _gateway(
    repository: Path,
    *,
    limits: ToolGatewayLimits | None = None,
) -> AgenticToolGateway:
    request = _request()
    return AgenticToolGateway(
        repository,
        request,
        limits=limits,
    )


def test_exposes_only_three_local_read_tools(repository: Path):
    definitions = _gateway(repository).tool_definitions()

    assert {item["name"] for item in definitions} == {
        "search_text",
        "read_file",
        "read_diff_hunk",
    }
    assert "rag" not in repr(definitions).lower()
    assert "shell" not in repr(definitions).lower()


@pytest.mark.asyncio
async def test_search_read_and_diff_hunk_are_bounded(repository: Path):
    gateway = _gateway(repository)

    searched = await gateway.invoke(
        "search_text",
        {"query": "gateway.charge", "path_pattern": "src/*.py"},
    )
    source = await gateway.invoke(
        "read_file",
        {"path": "src/payments.py", "start_line": 1, "end_line": 4},
    )
    hunk = await gateway.invoke(
        "read_diff_hunk",
        {"path": "src/payments.py", "line": 2},
    )

    assert searched["results"][0]["line"] == 4
    assert "super-secret-value" not in source["content"]
    assert "[REDACTED]" in source["content"]
    assert "+    validate(token)" in hunk["content"]
    assert source["path"] == "src/payments.py"
    assert hunk["path"] == "src/payments.py"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("read_file", {"path": "../outside.py"}),
        ("read_file", {"path": "/etc/passwd"}),
        ("search_text", {"query": "x", "path_pattern": "../*"}),
        ("rag_search", {"query": "not available"}),
    ],
)
async def test_rejects_escape_and_removed_tools(
    repository: Path, tool: str, arguments: dict
):
    gateway = _gateway(repository)

    with pytest.raises(AgenticToolError):
        await gateway.invoke(tool, arguments)


@pytest.mark.asyncio
async def test_sensitive_paths_are_never_searchable_or_readable(repository: Path):
    gateway = _gateway(repository)

    searched = await gateway.invoke("search_text", {"query": "must-not-leak"})
    assert searched["results"] == []
    with pytest.raises(AgenticToolError) as error:
        await gateway.invoke("read_file", {"path": ".env"})
    assert error.value.code == "SENSITIVE_PATH"


@pytest.mark.asyncio
async def test_literal_search_does_not_execute_regular_expressions(repository: Path):
    (repository / "src" / "literal.py").write_text(
        "value = 'a.*b'\naZZb\n", encoding="utf-8"
    )
    gateway = _gateway(repository)

    result = await gateway.invoke("search_text", {"query": "a.*b"})

    assert [(item["path"], item["line"]) for item in result["results"]] == [
        ("src/literal.py", 1)
    ]


@pytest.mark.asyncio
async def test_shared_call_budget_is_enforced(repository: Path):
    gateway = _gateway(
        repository,
        limits=ToolGatewayLimits(max_calls=1),
    )

    await gateway.invoke("search_text", {"query": "charge"})
    with pytest.raises(AgenticToolError) as error:
        await gateway.invoke("search_text", {"query": "charge"})
    assert error.value.code == "CALL_BUDGET_EXHAUSTED"
