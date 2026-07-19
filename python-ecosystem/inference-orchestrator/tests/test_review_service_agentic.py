"""ReviewService dispatch and workspace ownership for AGENTIC requests."""

import hashlib
import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from model.dtos import ReviewRequestDto
from service.review.review_service import ReviewService


def _archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("repo/src/app.py", "value = 1\n")
    return buffer.getvalue()


def _request(content: bytes) -> ReviewRequestDto:
    return ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="acme",
        projectVcsRepoSlug="repo",
        projectWorkspace="acme",
        projectNamespace="repo",
        aiProvider="OPENAI",
        aiModel="test-model",
        aiApiKey="test-key",
        reviewApproach="AGENTIC",
        rawDiff=(
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1 +1 @@\n"
            "-value = 0\n"
            "+value = 1\n"
        ),
        previousCommitHash="a" * 40,
        currentCommitHash="b" * 40,
        agenticRepository={
            "workspaceKey": "d" * 64,
            "snapshotSha": "b" * 40,
            "contentDigest": hashlib.sha256(content).hexdigest(),
            "byteLength": len(content),
        },
    )


def _service(root) -> ReviewService:
    with patch("service.review.review_service.RagClient"), patch(
        "service.review.review_service.get_rag_cache"
    ):
        service = ReviewService()
    service.AGENTIC_WORKSPACE_ROOT = str(root)
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_agentic_workspace_is_removed_after_success_or_failure(tmp_path, fail):
    content = _archive()
    request = _request(content)
    directory = tmp_path / request.agenticRepository.workspaceKey
    directory.mkdir()
    (directory / "repository.zip").write_bytes(content)
    service = _service(tmp_path)
    service._create_llm = MagicMock(return_value=object())

    engine = MagicMock()
    engine.review = AsyncMock()
    if fail:
        engine.review.side_effect = RuntimeError("model failed")
    else:
        engine.review.return_value = {"comment": "done", "issues": []}
    with patch(
        "service.review.review_service.AgenticReviewEngine",
        return_value=engine,
    ):
        result = await service.process_review_request(request)

    assert not directory.exists()
    if fail:
        assert result["result"]["status"] == "error"
        assert result["result"].get("issues", []) == []
    else:
        assert result["result"]["reviewApproach"] == "AGENTIC"


@pytest.mark.asyncio
async def test_classic_request_stays_on_existing_review_flow():
    service = _service("/tmp/unused-agentic-test-root")
    request = ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="acme",
        projectVcsRepoSlug="repo",
        projectWorkspace="acme",
        projectNamespace="repo",
        aiProvider="OPENAI",
        aiModel="test-model",
        aiApiKey="test-key",
    )
    service._process_review = AsyncMock(return_value={"result": {"issues": []}})

    result = await service.process_review_request(request)

    assert result == {"result": {"issues": []}}
    service._process_review.assert_awaited_once()


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "Dockerfile.observable"])
def test_runtime_user_matches_shared_agentic_workspace_owner(dockerfile):
    content = (Path(__file__).parents[1] / "src" / dockerfile).read_text()

    assert "groupadd --system --gid 1001 appuser" in content
    assert "useradd --system --uid 1001 --gid appuser appuser" in content
