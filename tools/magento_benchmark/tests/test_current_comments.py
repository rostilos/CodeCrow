from __future__ import annotations

import copy
import json

import pytest

from magento2_benchmark import current_comments
from magento2_benchmark.github import GitHubResponse
from magento2_benchmark.util import sha256_json


def _source_comment() -> dict:
    return {
        "id": 101,
        "review_id": 201,
        "reviewer": "reviewer",
        "author_association": "MEMBER",
        "created_at": "2026-07-01T10:00:00Z",
        "updated_at": "2026-07-01T10:05:00Z",
        "body": "Remove this unsafe fallback.",
        "url": (
            "https://github.com/magento/magento2/"
            "pull/123#discussion_r101"
        ),
        "original_commit_id": "a" * 40,
        "api_commit_id": "b" * 40,
        "path": "app/code/Magento/Test.php",
        "diff_hunk": "@@ -7,0 +8,3 @@\n+unsafe();",
        "side": "RIGHT",
        "line": 10,
        "start_line": 8,
        "raw_current_line": None,
        "raw_original_line": 10,
        "raw_current_start_line": None,
        "raw_original_start_line": 8,
        "raw_start_side": "RIGHT",
        "raw_position": None,
        "raw_original_position": 10,
    }


def _raw_root() -> dict:
    return {
        "id": 101,
        "pull_request_review_id": 201,
        "user": {"login": "reviewer", "type": "User"},
        "author_association": "MEMBER",
        "created_at": "2026-07-01T10:00:00Z",
        "updated_at": "2026-07-01T10:05:00Z",
        "body": "Remove this unsafe fallback.",
        "html_url": (
            "https://github.com/magento/magento2/"
            "pull/123#discussion_r101"
        ),
        "pull_request_url": (
            "https://api.github.com/repos/magento/magento2/pulls/123"
        ),
        "commit_id": "b" * 40,
        "original_commit_id": "a" * 40,
        "path": "app/code/Magento/Test.php",
        "diff_hunk": "@@ -7,0 +8,3 @@\n+unsafe();",
        "line": None,
        "original_line": 10,
        "start_line": None,
        "original_start_line": 8,
        "side": "RIGHT",
        "start_side": "RIGHT",
        "subject_type": "line",
        "position": None,
        "original_position": 10,
        "in_reply_to_id": None,
    }


def _raw_reply() -> dict:
    return {
        "id": 102,
        "pull_request_review_id": 202,
        "user": {"login": "author", "type": "User"},
        "body": "Fixed in the next commit.",
        "created_at": "2026-07-01T10:10:00Z",
        "updated_at": "2026-07-01T10:10:00Z",
        "path": "app/code/Magento/Test.php",
        "diff_hunk": "@@ -7,0 +8,3 @@\n+unsafe();",
        "commit_id": "b" * 40,
        "original_commit_id": "a" * 40,
        "html_url": (
            "https://github.com/magento/magento2/"
            "pull/123#discussion_r102"
        ),
        "pull_request_url": (
            "https://api.github.com/repos/magento/magento2/pulls/123"
        ),
        "in_reply_to_id": 101,
    }


class _FakeClient:
    api_url = "https://api.github.com"

    def __init__(self, comments: list[dict], *, offline: bool = False):
        self.comments = comments
        self.offline = offline
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, path, *, query=None, **_kwargs):
        self.calls.append((method, path, dict(query or {})))
        return GitHubResponse(
            value=copy.deepcopy(self.comments),
            headers={"ETag": '"current-comments"'},
            status=200,
        )


def _draft() -> dict:
    return {
        "repository": "magento/magento2",
        "cases": [
            {
                "pr_number": 123,
                "gold_comments": [_source_comment()],
            }
        ],
    }


def _write_draft(tmp_path, value: dict):
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_attest_current_comments_seals_complete_rest_thread(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(current_comments, "_validate_draft", lambda value: value)
    draft_path = _write_draft(tmp_path, _draft())
    output = tmp_path / "attestation.json"
    client = _FakeClient([_raw_root(), _raw_reply()])

    result = current_comments.attest_current_comments(
        client,
        draft_path=draft_path,
        output=output,
        repository="magento/magento2",
    )

    assert result["sourceMode"] == "live"
    assert result["currentSelectedCommentsVerified"] is True
    assert result["paperReady"] is False
    assert result["selectedRootCount"] == 1
    assert result["completeRestReplyCount"] == 1
    assert result["cases"][0]["threads"][0]["comments"][1]["id"] == 102
    assert client.calls == [
        (
            "GET",
            "/repos/magento/magento2/pulls/123/comments",
            {"per_page": 100, "page": 1},
        )
    ]
    assert json.loads(output.read_text(encoding="utf-8")) == result


def test_attest_current_comments_labels_offline_cache_only(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(current_comments, "_validate_draft", lambda value: value)
    draft_path = _write_draft(tmp_path, _draft())

    result = current_comments.attest_current_comments(
        _FakeClient([_raw_root()], offline=True),
        draft_path=draft_path,
        output=tmp_path / "attestation.json",
        repository="magento/magento2",
    )

    assert result["sourceMode"] == "cache-only"
    assert "does not prove when GitHub last served" in result["warning"]


def test_attest_current_comments_rejects_current_body_drift(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(current_comments, "_validate_draft", lambda value: value)
    draft_path = _write_draft(tmp_path, _draft())
    raw = _raw_root()
    raw["body"] = "Edited after the draft was frozen."

    with pytest.raises(ValueError, match="drifted from the draft"):
        current_comments.attest_current_comments(
            _FakeClient([raw]),
            draft_path=draft_path,
            output=tmp_path / "attestation.json",
            repository="magento/magento2",
        )


def test_attestation_rejects_resealed_selected_root_tampering(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(current_comments, "_validate_draft", lambda value: value)
    draft_path = _write_draft(tmp_path, _draft())
    result = current_comments.attest_current_comments(
        _FakeClient([_raw_root()]),
        draft_path=draft_path,
        output=tmp_path / "attestation.json",
        repository="magento/magento2",
    )
    tampered = copy.deepcopy(result)
    case = tampered["cases"][0]
    page = case["pages"][0]
    page["response"][0]["body"] = "Tampered body"
    page["responseDigest"] = sha256_json(page["response"])
    page_value = dict(page)
    page_value.pop("pageDigest")
    page["pageDigest"] = sha256_json(page_value)
    case["allReviewCommentsDigest"] = sha256_json(page["response"])
    case["selectedRoots"][0]["response"] = copy.deepcopy(page["response"][0])
    case["selectedRoots"][0]["responseDigest"] = sha256_json(page["response"][0])
    case["threads"][0]["comments"][0] = copy.deepcopy(page["response"][0])
    thread_value = dict(case["threads"][0])
    thread_value.pop("threadDigest")
    case["threads"][0]["threadDigest"] = sha256_json(thread_value)
    case_value = dict(case)
    case_value.pop("caseEvidenceDigest")
    case["caseEvidenceDigest"] = sha256_json(case_value)
    top_value = dict(tampered)
    top_value.pop("attestationDigest")
    tampered["attestationDigest"] = sha256_json(top_value)

    with pytest.raises(ValueError, match="drifted from the draft"):
        current_comments.validate_current_comment_attestation(
            tampered,
            draft_path=draft_path,
            repository="magento/magento2",
        )
