from __future__ import annotations

import json

import pytest

from magento2_benchmark.execution_corpus import build_execution_corpus
from magento2_benchmark.replay import (
    apply_plan,
    build_plan,
    validate_replay_attestation,
    validate_replay_attestation_freshness,
    validate_replay_lock,
    verify_replay,
)
from magento2_benchmark.util import sha256_json

from conftest import write_json


class FakeGitHub:
    def __init__(self, *, token: bool = True):
        self.token = token
        self.calls = []
        self.refs = {}
        self.pulls = {}
        self.next_pull = 1

    def require_token(self):
        self.calls.append(("require_token",))
        if not self.token:
            raise RuntimeError("token required")

    def get(self, path):
        self.calls.append(("get", path))
        if "/pulls/" in path:
            number = int(path.rsplit("/", 1)[1])
            return next(
                pull
                for pull in self.pulls.values()
                if pull["number"] == number
            )
        return {
            "id": 101,
            "node_id": "R_fixture",
            "full_name": "benchmark-owner/magento2",
            "fork": True,
            "parent": {"full_name": "magento/magento2"},
        }

    def get_ref(self, repository, ref):
        self.calls.append(("get_ref", repository, ref))
        if ref not in self.refs:
            raise RuntimeError("HTTP 404")
        sha = self.refs[ref]
        return {
            "ref": f"refs/heads/{ref}",
            "object": {
                "sha": sha,
                "type": "commit",
                "url": (
                    "https://api.github.com/repos/"
                    f"{repository}/git/commits/{sha}"
                ),
            },
        }

    def create_ref(self, repository, ref, sha):
        self.calls.append(("create_ref", repository, ref, sha))
        self.refs[ref] = sha
        return {
            "ref": f"refs/heads/{ref}",
            "object": {
                "sha": sha,
                "type": "commit",
                "url": (
                    "https://api.github.com/repos/"
                    f"{repository}/git/commits/{sha}"
                ),
            },
        }

    def find_pull(self, repository, *, owner, head):
        self.calls.append(("find_pull", repository, owner, head))
        return self.pulls.get(head)

    def create_pull(self, repository, *, title, body, base, head):
        self.calls.append(("create_pull", repository, title, body, base, head))
        head_ref = head.split(":", 1)[1]
        pull = {
            "id": 10_000 + self.next_pull,
            "node_id": f"PR_fixture_{self.next_pull}",
            "number": self.next_pull,
            "url": (
                f"https://api.github.com/repos/{repository}"
                f"/pulls/{self.next_pull}"
            ),
            "html_url": f"https://github.com/{repository}/pull/{self.next_pull}",
            "state": "open",
            "base": {
                "ref": base,
                "sha": self.refs[base],
                "repo": {"full_name": repository},
            },
            "head": {
                "ref": head_ref,
                "sha": self.refs[head_ref],
                "repo": {"full_name": repository},
            },
        }
        self.next_pull += 1
        self.pulls[head_ref] = pull
        return pull


def test_plan_is_blinded_to_reviewer_and_source_pr_evidence(corpus_factory):
    corpus = corpus_factory()
    case = corpus["cases"][0]
    case["sourcePr"]["title"] = "TOP SECRET SOURCE TITLE"
    case["goldenComments"][0]["reviewer"] = "SensitiveReviewer"
    case["goldenComments"][0]["body"] = "SENSITIVE REVIEW EVIDENCE"
    from magento2_benchmark.corpus import attach_corpus_digest
    from magento2_benchmark.util import sha256_text

    case["goldenComments"][0]["bodySha256"] = sha256_text(
        case["goldenComments"][0]["body"]
    )
    corpus = attach_corpus_digest(corpus)

    plan = build_plan(corpus, fork_repository="benchmark-owner/magento2")
    serialized = json.dumps(plan)

    assert plan["cases"][0]["title"] == "Magento 2 review benchmark fixture 001"
    assert "TOP SECRET SOURCE TITLE" not in serialized
    assert "SensitiveReviewer" not in serialized
    assert "SENSITIVE REVIEW EVIDENCE" not in serialized
    assert "#discussion_" not in serialized
    assert plan["cases"][0]["baseSha"] == case["snapshot"]["baseSha"]
    assert plan["cases"][0]["headSha"] == case["snapshot"]["headSha"]


def test_apply_requires_exact_confirmation_before_any_mutation(
    tmp_path,
    corpus_factory,
):
    plan = build_plan(
        corpus_factory(),
        fork_repository="benchmark-owner/magento2",
    )
    plan_path = write_json(tmp_path / "plan.json", plan)
    client = FakeGitHub()

    with pytest.raises(ValueError, match="exactly match"):
        apply_plan(
            client,
            plan_path=plan_path,
            output=tmp_path / "lock.json",
            confirm_fork="other-owner/magento2",
        )

    assert client.calls == []
    assert not (tmp_path / "lock.json").exists()


def test_apply_is_idempotent_for_matching_refs_and_pull_requests(
    tmp_path,
    corpus_factory,
):
    plan = build_plan(
        corpus_factory(),
        fork_repository="benchmark-owner/magento2",
    )
    plan_path = write_json(tmp_path / "plan.json", plan)
    client = FakeGitHub()

    first = apply_plan(
        client,
        plan_path=plan_path,
        output=tmp_path / "lock-1.json",
        confirm_fork="benchmark-owner/magento2",
    )
    create_refs_after_first = sum(
        call[0] == "create_ref" for call in client.calls
    )
    create_pulls_after_first = sum(
        call[0] == "create_pull" for call in client.calls
    )

    second = apply_plan(
        client,
        plan_path=plan_path,
        output=tmp_path / "lock-2.json",
        confirm_fork="benchmark-owner/magento2",
    )

    assert create_refs_after_first == 100
    assert create_pulls_after_first == 50
    assert sum(call[0] == "create_ref" for call in client.calls) == 100
    assert sum(call[0] == "create_pull" for call in client.calls) == 50
    assert [
        (case["caseId"], case["baseSha"], case["headSha"], case["forkPrNumber"])
        for case in first["cases"]
    ] == [
        (case["caseId"], case["baseSha"], case["headSha"], case["forkPrNumber"])
        for case in second["cases"]
    ]
    assert first["plan"] == plan
    assert validate_replay_lock(first, corpus_factory()) == {
        case["caseId"]: case for case in first["cases"]
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda lock: lock["cases"].pop(),
            "exactly match corpus case order and set",
        ),
        (
            lambda lock: lock["cases"][0].__setitem__(
                "baseRef",
                "benchmark/wrong/base",
            ),
            "baseRef does not match corpus",
        ),
        (
            lambda lock: lock["cases"][0].__setitem__(
                "headSha",
                "f" * 40,
            ),
            "headSha does not match corpus",
        ),
        (
            lambda lock: lock["cases"][0].__setitem__(
                "forkPrNumber",
                0,
            ),
            "forkPrNumber must be a positive integer",
        ),
        (
            lambda lock: lock["cases"][0].__setitem__(
                "forkPrUrl",
                "https://github.com/other/magento2/pull/1",
            ),
            "forkPrUrl is not canonical",
        ),
        (
            lambda lock: lock.__setitem__("planDigest", "f" * 64),
            "does not bind its embedded plan",
        ),
        (
            lambda lock: lock.__setitem__(
                "forkRepository",
                "other-owner/magento2",
            ),
            "forkRepository is not lock/corpus bound",
        ),
    ],
)
def test_lock_rejects_self_consistent_identity_tampering(
    tmp_path,
    corpus_factory,
    mutate,
    message,
):
    corpus = corpus_factory()
    plan = build_plan(
        corpus,
        fork_repository="benchmark-owner/magento2",
    )
    lock = apply_plan(
        FakeGitHub(),
        plan_path=write_json(tmp_path / "plan.json", plan),
        output=tmp_path / "lock.json",
        confirm_fork="benchmark-owner/magento2",
    )

    mutate(lock)
    lock["lockDigest"] = sha256_json(
        {key: value for key, value in lock.items() if key != "lockDigest"}
    )

    with pytest.raises(ValueError, match=message):
        validate_replay_lock(lock, corpus)


def test_lock_rejects_a_rehashed_embedded_plan_that_drifted_from_corpus(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    plan = build_plan(
        corpus,
        fork_repository="benchmark-owner/magento2",
    )
    lock = apply_plan(
        FakeGitHub(),
        plan_path=write_json(tmp_path / "plan.json", plan),
        output=tmp_path / "lock.json",
        confirm_fork="benchmark-owner/magento2",
    )
    lock["plan"]["cases"][0]["baseRef"] = "benchmark/wrong/base"
    lock["plan"]["planDigest"] = sha256_json(
        {
            key: value
            for key, value in lock["plan"].items()
            if key != "planDigest"
        }
    )
    lock["planDigest"] = lock["plan"]["planDigest"]
    lock["lockDigest"] = sha256_json(
        {key: value for key, value in lock.items() if key != "lockDigest"}
    )

    with pytest.raises(ValueError, match="do not exactly match the corpus"):
        validate_replay_lock(lock, corpus)


def test_verify_replay_live_checks_every_ref_and_pr_and_seals_attestation(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    plan = build_plan(
        corpus,
        fork_repository="benchmark-owner/magento2",
    )
    client = FakeGitHub()
    lock = apply_plan(
        client,
        plan_path=write_json(tmp_path / "plan.json", plan),
        output=tmp_path / "lock.json",
        confirm_fork="benchmark-owner/magento2",
    )
    client.calls.clear()

    attestation = verify_replay(
        client,
        execution_corpus_path=write_json(
            tmp_path / "execution-corpus.json",
            build_execution_corpus(corpus),
        ),
        replay_lock_path=tmp_path / "lock.json",
        output=tmp_path / "attestation.json",
    )

    assert validate_replay_attestation(attestation, lock, corpus) == (
        attestation["attestationDigest"]
    )
    assert attestation["replayLockDigest"] == lock["lockDigest"]
    assert attestation["planDigest"] == lock["planDigest"]
    assert attestation["repositoryObservation"] == {
        "apiPath": "/repos/benchmark-owner/magento2",
        "repositoryId": 101,
        "nodeId": "R_fixture",
        "fullName": "benchmark-owner/magento2",
        "fork": True,
        "upstreamRepository": "magento/magento2",
    }
    assert len(attestation["cases"]) == 50
    assert sum(call[0] == "get_ref" for call in client.calls) == 100
    assert sum(
        call[0] == "get" and "/pulls/" in call[1]
        for call in client.calls
    ) == 50


def test_replay_attestation_rejects_rehashed_observation_drift(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    plan = build_plan(
        corpus,
        fork_repository="benchmark-owner/magento2",
    )
    client = FakeGitHub()
    lock = apply_plan(
        client,
        plan_path=write_json(tmp_path / "plan.json", plan),
        output=tmp_path / "lock.json",
        confirm_fork="benchmark-owner/magento2",
    )
    attestation = verify_replay(
        client,
        execution_corpus_path=write_json(
            tmp_path / "execution-corpus.json",
            build_execution_corpus(corpus),
        ),
        replay_lock_path=tmp_path / "lock.json",
        output=tmp_path / "attestation.json",
    )
    attestation["cases"][0]["baseRef"]["sha"] = "f" * 40
    attestation["attestationDigest"] = sha256_json(
        {
            key: value
            for key, value in attestation.items()
            if key != "attestationDigest"
        }
    )

    with pytest.raises(ValueError, match="baseRef does not match"):
        validate_replay_attestation(attestation, lock, corpus)


def test_verify_replay_fails_closed_on_live_ref_drift(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    plan = build_plan(
        corpus,
        fork_repository="benchmark-owner/magento2",
    )
    client = FakeGitHub()
    lock = apply_plan(
        client,
        plan_path=write_json(tmp_path / "plan.json", plan),
        output=tmp_path / "lock.json",
        confirm_fork="benchmark-owner/magento2",
    )
    client.refs[lock["cases"][0]["baseRef"]] = "f" * 40

    with pytest.raises(RuntimeError, match="wrong commit"):
        verify_replay(
            client,
            execution_corpus_path=write_json(
                tmp_path / "execution-corpus.json",
                build_execution_corpus(corpus),
            ),
            replay_lock_path=tmp_path / "lock.json",
            output=tmp_path / "attestation.json",
        )

    assert not (tmp_path / "attestation.json").exists()


def test_verify_replay_requires_a_token_before_live_requests(
    tmp_path,
    corpus_factory,
):
    corpus = corpus_factory()
    plan = build_plan(
        corpus,
        fork_repository="benchmark-owner/magento2",
    )
    client = FakeGitHub()
    lock = apply_plan(
        client,
        plan_path=write_json(tmp_path / "plan.json", plan),
        output=tmp_path / "lock.json",
        confirm_fork="benchmark-owner/magento2",
    )
    client = FakeGitHub(token=False)

    with pytest.raises(RuntimeError, match="token is required"):
        verify_replay(
            client,
            execution_corpus_path=write_json(
                tmp_path / "execution-corpus.json",
                build_execution_corpus(corpus),
            ),
            replay_lock_path=write_json(tmp_path / "lock-copy.json", lock),
            output=tmp_path / "attestation.json",
        )

    assert client.calls == []


def test_apply_refuses_to_overwrite_a_divergent_existing_ref(
    tmp_path,
    corpus_factory,
):
    plan = build_plan(
        corpus_factory(),
        fork_repository="benchmark-owner/magento2",
    )
    first_case = plan["cases"][0]
    client = FakeGitHub()
    client.refs[first_case["baseRef"]] = "f" * 40
    plan_path = write_json(tmp_path / "plan.json", plan)

    with pytest.raises(RuntimeError, match="will not overwrite"):
        apply_plan(
            client,
            plan_path=plan_path,
            output=tmp_path / "lock.json",
            confirm_fork="benchmark-owner/magento2",
        )

    assert not any(call[0] == "create_ref" for call in client.calls)
    assert not (tmp_path / "lock.json").exists()


def test_apply_rejects_tampered_plan_before_token_or_network(
    tmp_path,
    corpus_factory,
):
    plan = build_plan(
        corpus_factory(),
        fork_repository="benchmark-owner/magento2",
    )
    plan["cases"][0]["headSha"] = "f" * 40
    plan_path = write_json(tmp_path / "plan.json", plan)
    client = FakeGitHub()

    with pytest.raises(ValueError, match="digest mismatch"):
        apply_plan(
            client,
            plan_path=plan_path,
            output=tmp_path / "lock.json",
            confirm_fork="benchmark-owner/magento2",
        )

    assert client.calls == []


def test_apply_rejects_non_fork_before_creating_refs(
    tmp_path,
    corpus_factory,
):
    plan = build_plan(
        corpus_factory(),
        fork_repository="benchmark-owner/magento2",
    )
    client = FakeGitHub()
    client.get = lambda path: {
        "fork": False,
        "parent": {"full_name": "another/project"},
    }

    with pytest.raises(ValueError, match="not a GitHub fork"):
        apply_plan(
            client,
            plan_path=write_json(tmp_path / "plan.json", plan),
            output=tmp_path / "lock.json",
            confirm_fork="benchmark-owner/magento2",
        )

    assert not any(call[0] == "create_ref" for call in client.calls)
def test_replay_attestation_freshness_is_bound_to_run_start():
    attestation = {"collectedAt": "2026-07-29T12:00:00Z"}

    assert validate_replay_attestation_freshness(
        attestation,
        reference_at="2026-07-29T12:00:30Z",
        max_age_seconds=60,
    ) == 30
    with pytest.raises(ValueError, match="stale"):
        validate_replay_attestation_freshness(
            attestation,
            reference_at="2026-07-29T12:01:01Z",
            max_age_seconds=60,
        )
    with pytest.raises(ValueError, match="after"):
        validate_replay_attestation_freshness(
            attestation,
            reference_at="2026-07-29T11:59:59Z",
            max_age_seconds=60,
        )
    with pytest.raises(ValueError, match="positive"):
        validate_replay_attestation_freshness(
            attestation,
            reference_at="2026-07-29T12:00:00Z",
            max_age_seconds=0,
        )
    with pytest.raises(ValueError, match="positive"):
        validate_replay_attestation_freshness(
            attestation,
            reference_at="2026-07-29T12:00:00Z",
            max_age_seconds=float("nan"),
        )
