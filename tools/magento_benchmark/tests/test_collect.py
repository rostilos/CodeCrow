from __future__ import annotations

import copy
import subprocess

import pytest

from magento2_benchmark.collect import (
    _ancestry_evidence,
    _validated_curation_path_transition,
    _validated_source_archive_evidence,
    build_discovery_selection_linkage,
    discover,
    link_discovery_selection,
    validate_discovery,
    validate_discovery_selection_linkage,
)
from magento2_benchmark.curation import _curation_path_evidence
from magento2_benchmark.github import GitHubResponse
from magento2_benchmark.path_transition import resolve_path_transition
from magento2_benchmark.util import sha256_json, write_json


def _git(repository, *args):
    return subprocess.check_output(
        ["git", "-C", str(repository), *args],
        text=True,
    ).strip()


def _commit(repository, message, path, content):
    (repository / path).write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", path],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", message],
        check=True,
    )
    return _git(repository, "rev-parse", "HEAD")


class _DiscoveryClient:
    api_url = "https://api.github.com"

    def __init__(self, pages, *, offline=False):
        self.offline = offline
        self._pages = list(pages)
        self.requests = []

    def request(self, method, path, *, query):
        self.requests.append(
            {
                "method": method,
                "path": path,
                "query": dict(query),
            }
        )
        if not self._pages:
            raise AssertionError("unexpected discovery request")
        value = self._pages.pop(0)
        return GitHubResponse(
            value=value,
            headers={
                "ETag": f'"page-{len(self.requests)}"',
                "Last-Modified": "Wed, 29 Jul 2026 00:00:00 GMT",
            },
            status=200,
        )


def _review_comment(
    comment_id,
    *,
    pull_request,
    head,
    reviewer,
    body,
    created_at,
):
    return {
        "id": comment_id,
        "user": {"type": "User", "login": reviewer},
        "in_reply_to_id": None,
        "pull_request_review_id": comment_id + 10_000,
        "original_commit_id": head,
        "path": "app/code/Magento/Fixture.php",
        "original_side": "RIGHT",
        "side": "RIGHT",
        "body": body,
        "pull_request_url": (
            "https://api.github.com/repos/magento/magento2/pulls/"
            f"{pull_request}"
        ),
        "created_at": created_at,
        "updated_at": created_at,
    }


def _discovery_fixture(tmp_path, *, offline=False):
    head_a = "a" * 40
    head_b = "b" * 40
    comments = [
        _review_comment(
            201,
            pull_request=102,
            head=head_b,
            reviewer="reviewer-b",
            body="Please change this implementation.",
            created_at="2026-07-29T04:00:00Z",
        ),
        _review_comment(
            202,
            pull_request=102,
            head=head_b,
            reviewer="reviewer-a",
            body="This needs a test.",
            created_at="2026-07-29T03:00:00Z",
        ),
        _review_comment(
            101,
            pull_request=101,
            head=head_a,
            reviewer="reviewer-a",
            body="Looks incorrect; please fix it.",
            created_at="2026-07-29T02:00:00Z",
        ),
        {
            **_review_comment(
                301,
                pull_request=103,
                head="c" * 40,
                reviewer="automation",
                body="Please change this.",
                created_at="2026-07-29T01:00:00Z",
            ),
            "user": {"type": "Bot", "login": "automation"},
        },
    ]
    client = _DiscoveryClient([comments], offline=offline)
    artifact = discover(
        client,
        repository="magento/magento2",
        pages=3,
        output=tmp_path / "discovery.json",
    )
    return artifact, client


def _reseal_discovery(value):
    value["discoveryDigest"] = sha256_json(
        {
            key: item
            for key, item in value.items()
            if key != "discoveryDigest"
        }
    )


def _reseal_page(page):
    page["pageDigest"] = sha256_json(
        {
            key: item
            for key, item in page.items()
            if key != "pageDigest"
        }
    )


def _released_selection(artifact):
    cases = []
    for index, candidate in enumerate(
        reversed(artifact["candidates"]),
        start=1,
    ):
        cases.append(
            {
                "caseId": f"m2b-{index:03d}",
                "pullRequest": candidate["pullRequest"],
                "headSha": candidate["headSha"],
                "commentIds": [candidate["commentIds"][0]],
            }
        )
    selection = {
        "kind": "codecrow-magento2-review-selection",
        "generatedAt": "2026-07-29T05:00:00Z",
        "cases": cases,
    }
    selection["selectionDigest"] = sha256_json(selection)
    return selection


def _reseal_linkage(value):
    value["linkageDigest"] = sha256_json(
        {
            key: item
            for key, item in value.items()
            if key != "linkageDigest"
        }
    )


def test_discovery_seals_raw_request_pages_and_recomputable_candidate_pool(
    tmp_path,
):
    artifact, client = _discovery_fixture(tmp_path)

    assert client.requests == [
        {
            "method": "GET",
            "path": "/repos/magento/magento2/pulls/comments",
            "query": {
                "sort": "created",
                "direction": "desc",
                "per_page": 100,
                "page": 1,
            },
        }
    ]
    assert artifact["sourceMode"] == "live"
    assert artifact["pages"] == 3
    assert artifact["pageCount"] == 1
    assert artifact["terminationReason"] == "short_page"
    assert artifact["rawCommentCount"] == 4
    assert artifact["rejectedCommentCount"] == 1
    assert artifact["candidateCount"] == 2
    assert artifact["candidates"][0]["pullRequest"] == 102
    assert artifact["candidates"][0]["commentIds"] == [201, 202]
    assert artifact["rawPages"][0]["responseDigest"] == sha256_json(
        artifact["rawPages"][0]["response"]
    )
    assert validate_discovery(
        artifact,
        repository="magento/magento2",
    ) == artifact


def test_discovery_labels_explicit_offline_cache_provenance(tmp_path):
    artifact, _ = _discovery_fixture(tmp_path, offline=True)

    assert artifact["sourceMode"] == "cache-only"
    validate_discovery(artifact)


def test_discovery_binds_multi_page_request_and_digest_chain(tmp_path):
    first_page = [
        _review_comment(
            1_000 + index,
            pull_request=104,
            head="d" * 40,
            reviewer="reviewer",
            body="Please change this.",
            created_at="2026-07-29T04:00:00Z",
        )
        for index in range(100)
    ]
    final_comment = _review_comment(
        2_000,
        pull_request=104,
        head="d" * 40,
        reviewer="reviewer",
        body="Please add the missing test.",
        created_at="2026-07-29T03:00:00Z",
    )
    client = _DiscoveryClient([first_page, [final_comment]])

    artifact = discover(
        client,
        repository="magento/magento2",
        pages=5,
        output=tmp_path / "discovery.json",
    )

    assert artifact["pageCount"] == 2
    assert artifact["terminationReason"] == "short_page"
    assert artifact["rawPages"][1]["previousPageDigest"] == (
        artifact["rawPages"][0]["pageDigest"]
    )
    assert artifact["candidates"][0]["reviewCommentCount"] == 101
    validate_discovery(artifact)


def test_discovery_rejects_resealed_page_chain_drift(tmp_path):
    first_page = [
        _review_comment(
            3_000 + index,
            pull_request=105,
            head="e" * 40,
            reviewer="reviewer",
            body="Please change this.",
            created_at="2026-07-29T04:00:00Z",
        )
        for index in range(100)
    ]
    client = _DiscoveryClient(
        [
            first_page,
            [
                _review_comment(
                    4_000,
                    pull_request=105,
                    head="e" * 40,
                    reviewer="reviewer",
                    body="Please add a test.",
                    created_at="2026-07-29T03:00:00Z",
                )
            ],
        ]
    )
    artifact = discover(
        client,
        repository="magento/magento2",
        pages=5,
        output=tmp_path / "discovery.json",
    )
    hostile = copy.deepcopy(artifact)
    hostile["rawPages"][1]["previousPageDigest"] = "f" * 64
    _reseal_page(hostile["rawPages"][1])
    _reseal_discovery(hostile)

    with pytest.raises(ValueError, match="request/response chain"):
        validate_discovery(hostile)


def test_discovery_rejects_resealed_query_drift(tmp_path):
    artifact, _ = _discovery_fixture(tmp_path)
    hostile = copy.deepcopy(artifact)
    request = hostile["rawPages"][0]["request"]
    request["query"]["direction"] = "asc"
    request["url"] = request["url"].replace("direction=desc", "direction=asc")
    _reseal_page(hostile["rawPages"][0])
    _reseal_discovery(hostile)

    with pytest.raises(
        ValueError,
        match="request/response chain",
    ):
        validate_discovery(hostile)


def test_discovery_rejects_resealed_filter_policy_drift(tmp_path):
    artifact, _ = _discovery_fixture(tmp_path)
    hostile = copy.deepcopy(artifact)
    hostile["filterPolicy"]["version"] = "attacker-controlled-policy"
    _reseal_discovery(hostile)

    with pytest.raises(ValueError, match="rejection/filter policy drift"):
        validate_discovery(hostile)


def test_discovery_rejects_resealed_candidate_order_drift(tmp_path):
    artifact, _ = _discovery_fixture(tmp_path)
    hostile = copy.deepcopy(artifact)
    hostile["candidates"].reverse()
    hostile["candidateSetDigest"] = sha256_json(hostile["candidates"])
    _reseal_discovery(hostile)

    with pytest.raises(ValueError, match="candidate set/order drift"):
        validate_discovery(hostile)


def test_discovery_rejects_resealed_candidate_content_drift(tmp_path):
    artifact, _ = _discovery_fixture(tmp_path)
    hostile = copy.deepcopy(artifact)
    hostile["candidates"][0]["reviewCommentCount"] = 99
    hostile["candidateSetDigest"] = sha256_json(hostile["candidates"])
    _reseal_discovery(hostile)

    with pytest.raises(ValueError, match="candidate set/order drift"):
        validate_discovery(hostile)


def test_discovery_selection_link_binds_order_and_candidate_membership(
    tmp_path,
):
    artifact, _ = _discovery_fixture(tmp_path)
    selection = _released_selection(artifact)

    linkage = build_discovery_selection_linkage(
        artifact,
        selection=selection,
    )

    assert [
        item["pullRequest"] for item in linkage["selectedCandidates"]
    ] == [101, 102]
    assert validate_discovery_selection_linkage(
        linkage,
        discovery=artifact,
        selection=selection,
    ) == linkage


def test_discovery_selection_link_file_workflow(tmp_path):
    artifact, _ = _discovery_fixture(tmp_path)
    selection = _released_selection(artifact)
    discovery_path = tmp_path / "discovery-input.json"
    selection_path = tmp_path / "selection.json"
    output = tmp_path / "linkage.json"

    write_json(discovery_path, artifact)
    write_json(selection_path, selection)

    linkage = link_discovery_selection(
        discovery_path=discovery_path,
        selection_path=selection_path,
        output=output,
    )

    assert output.exists()
    assert validate_discovery_selection_linkage(
        linkage,
        discovery=artifact,
        selection=selection,
    ) == linkage


def test_discovery_selection_link_rejects_resealed_selection_order_drift(
    tmp_path,
):
    artifact, _ = _discovery_fixture(tmp_path)
    selection = _released_selection(artifact)
    linkage = build_discovery_selection_linkage(
        artifact,
        selection=selection,
    )
    hostile = copy.deepcopy(linkage)
    hostile["selectedCandidates"].reverse()
    hostile["selectedCandidateDigest"] = sha256_json(
        hostile["selectedCandidates"]
    )
    _reseal_linkage(hostile)

    with pytest.raises(ValueError, match="linkage drift"):
        validate_discovery_selection_linkage(
            hostile,
            discovery=artifact,
            selection=selection,
        )


def test_discovery_selection_link_rejects_resealed_comment_drift(tmp_path):
    artifact, _ = _discovery_fixture(tmp_path)
    selection = _released_selection(artifact)
    linkage = build_discovery_selection_linkage(
        artifact,
        selection=selection,
    )
    hostile = copy.deepcopy(linkage)
    hostile["selectedCandidates"][1]["selectedCommentIds"] = [202]
    hostile["selectedCandidateDigest"] = sha256_json(
        hostile["selectedCandidates"]
    )
    _reseal_linkage(hostile)

    with pytest.raises(ValueError, match="linkage drift"):
        validate_discovery_selection_linkage(
            hostile,
            discovery=artifact,
            selection=selection,
        )


def _ancestry_graph(tmp_path):
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Benchmark Test"],
        check=True,
    )
    base = _commit(repository, "base", "base.txt", "base\n")
    default_branch = _git(repository, "branch", "--show-current")
    subprocess.run(
        ["git", "-C", str(repository), "checkout", "-q", "-b", "feature"],
        check=True,
    )
    reviewed_head = _commit(
        repository,
        "reviewed head",
        "feature.txt",
        "reviewed\n",
    )
    final_head = _commit(
        repository,
        "final head",
        "feature.txt",
        "fixed\n",
    )
    subprocess.run(
        ["git", "-C", str(repository), "checkout", "-q", default_branch],
        check=True,
    )
    first_parent = _commit(
        repository,
        "mainline work",
        "mainline.txt",
        "mainline\n",
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "merge",
            "-q",
            "--no-ff",
            "feature",
            "-m",
            "merge feature",
        ],
        check=True,
    )
    merge_commit = _git(repository, "rev-parse", "HEAD")
    cutoff = _commit(repository, "cutoff", "cutoff.txt", "cutoff\n")
    return {
        "repository": repository,
        "base": base,
        "reviewed": reviewed_head,
        "final": final_head,
        "first_parent": first_parent,
        "merge": merge_commit,
        "cutoff": cutoff,
    }


def _path_transition_graph(tmp_path, operation):
    repository = tmp_path / f"repository-{operation}"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Benchmark Test"],
        check=True,
    )
    checkpoint = _commit(
        repository,
        "reviewed checkpoint",
        "fixture.php",
        "<?php\nbad();\n",
    )
    if operation == "modified":
        final = _commit(
            repository,
            "fix reviewed path",
            "fixture.php",
            "<?php\nfixed();\n",
        )
        final_path = "fixture.php"
    elif operation == "renamed":
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "mv",
                "fixture.php",
                "renamed.php",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-q", "-m", "rename path"],
            check=True,
        )
        final = _git(repository, "rev-parse", "HEAD")
        final_path = "renamed.php"
    else:
        subprocess.run(
            ["git", "-C", str(repository), "rm", "-q", "fixture.php"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-q", "-m", "delete path"],
            check=True,
        )
        final = _git(repository, "rev-parse", "HEAD")
        final_path = None
    return repository, checkpoint, final, final_path


@pytest.mark.parametrize("operation", ["modified", "renamed", "deleted"])
def test_path_transition_resolves_modified_renamed_and_deleted_states(
    tmp_path,
    operation,
):
    repository, checkpoint, final, final_path = _path_transition_graph(
        tmp_path,
        operation,
    )

    transition, diff = resolve_path_transition(
        repository,
        checkpoint_sha=checkpoint,
        final_sha=final,
        source_path="fixture.php",
    )

    assert transition["status"] == operation
    assert transition["sourcePath"] == "fixture.php"
    assert transition["finalPath"] == final_path
    assert transition["diffSha256"]
    assert diff
    if operation == "renamed":
        assert transition["renameSimilarity"] == 100
        assert transition["finalBlobOid"] == transition["checkpointBlobOid"]
    elif operation == "deleted":
        assert transition["renameSimilarity"] is None
        assert transition["finalBlobOid"] is None
    else:
        assert transition["renameSimilarity"] is None
        assert transition["finalBlobOid"] != transition["checkpointBlobOid"]


@pytest.mark.parametrize("operation", ["modified", "renamed", "deleted"])
def test_curation_path_evidence_exposes_exact_final_blob_and_source(
    tmp_path,
    operation,
):
    repository, checkpoint, final, final_path = _path_transition_graph(
        tmp_path,
        operation,
    )

    evidence = _curation_path_evidence(
        repository,
        checkpoint_sha=checkpoint,
        final_sha=final,
        source_path="fixture.php",
        source_line=2,
    )

    assert evidence["pathTransition"]["status"] == operation
    assert evidence["checkpointSource"]["available"] is True
    assert evidence["checkpointSource"]["path"] == "fixture.php"
    if operation == "deleted":
        assert evidence["finalSource"] == {
            "available": False,
            "path": None,
            "blobOid": None,
            "startLine": None,
            "endLine": None,
            "content": "",
        }
    else:
        assert evidence["finalSource"]["available"] is True
        assert evidence["finalSource"]["path"] == final_path
        assert evidence["finalSource"]["blobOid"] == evidence[
            "pathTransition"
        ]["finalBlobOid"]
        assert "fixed();" in evidence["finalSource"]["content"] or (
            operation == "renamed"
            and "bad();" in evidence["finalSource"]["content"]
        )


def test_materialization_recomputes_and_rejects_stale_packet_path_evidence(
    tmp_path,
):
    repository, checkpoint, final, _ = _path_transition_graph(
        tmp_path,
        "modified",
    )
    transition, _ = resolve_path_transition(
        repository,
        checkpoint_sha=checkpoint,
        final_sha=final,
        source_path="fixture.php",
    )
    annotation = {
        "fixEvidence": [
            {
                "kind": "code_change",
                "detail": "The reviewed path changed.",
                "artifactDigest": transition["diffSha256"],
            },
            {
                "kind": "thread",
                "detail": "The reviewer accepted the change.",
                "artifactDigest": "f" * 64,
            },
        ]
    }

    assert _validated_curation_path_transition(
        repository,
        checkpoint_sha=checkpoint,
        final_sha=final,
        source_path="fixture.php",
        source_evidence={"pathTransition": transition},
        annotation=annotation,
        paper_ready=True,
    ) == transition

    fabricated = copy.deepcopy(transition)
    fabricated["diffSha256"] = "a" * 64
    with pytest.raises(ValueError, match="does not match.*Git diff"):
        _validated_curation_path_transition(
            repository,
            checkpoint_sha=checkpoint,
            final_sha=final,
            source_path="fixture.php",
            source_evidence={"pathTransition": fabricated},
            annotation=annotation,
            paper_ready=True,
        )

    stale_annotation = copy.deepcopy(annotation)
    stale_annotation["fixEvidence"][0]["artifactDigest"] = "b" * 64
    with pytest.raises(ValueError, match="code-change evidence"):
        _validated_curation_path_transition(
            repository,
            checkpoint_sha=checkpoint,
            final_sha=final,
            source_path="fixture.php",
            source_evidence={"pathTransition": transition},
            annotation=stale_annotation,
            paper_ready=True,
        )


def test_paper_materialization_requires_packet_path_transition(tmp_path):
    repository, checkpoint, final, _ = _path_transition_graph(
        tmp_path,
        "modified",
    )

    with pytest.raises(ValueError, match="no bound.*path transition"):
        _validated_curation_path_transition(
            repository,
            checkpoint_sha=checkpoint,
            final_sha=final,
            source_path="fixture.php",
            source_evidence={},
            annotation={"fixEvidence": []},
            paper_ready=True,
        )


def test_materialization_proves_and_digests_full_review_ancestry(tmp_path):
    graph = _ancestry_graph(tmp_path)

    evidence = _ancestry_evidence(
        graph["repository"],
        base_sha=graph["base"],
        reviewed_head_sha=graph["reviewed"],
        final_head_sha=graph["final"],
        merge_commit_sha=graph["merge"],
        mainline_cutoff_sha=graph["cutoff"],
    )

    assert evidence["mergeParents"] == [
        graph["first_parent"],
        graph["final"],
    ]
    assert all(evidence["checks"].values())
    digest_value = dict(evidence)
    declared = digest_value.pop("evidenceDigest")
    assert declared == sha256_json(digest_value)


def test_materialization_rejects_unproven_mainline_cutoff(tmp_path):
    graph = _ancestry_graph(tmp_path)

    with pytest.raises(RuntimeError):
        _ancestry_evidence(
            graph["repository"],
            base_sha=graph["base"],
            reviewed_head_sha=graph["reviewed"],
            final_head_sha=graph["final"],
            merge_commit_sha=graph["merge"],
            mainline_cutoff_sha=graph["final"],
        )


def test_materialization_requires_final_head_as_second_merge_parent(tmp_path):
    graph = _ancestry_graph(tmp_path)

    with pytest.raises(ValueError, match="second parent"):
        _ancestry_evidence(
            graph["repository"],
            base_sha=graph["base"],
            reviewed_head_sha=graph["reviewed"],
            final_head_sha=graph["reviewed"],
            merge_commit_sha=graph["merge"],
            mainline_cutoff_sha=graph["cutoff"],
        )


def test_materialization_rejects_source_object_drift_after_release():
    pull = {"number": 123, "state": "closed"}
    comment = {
        "id": 456,
        "path": "app/code/Magento/Fixture.php",
        "body": "Please fix this.",
    }
    review = {"id": 789, "state": "COMMENTED"}
    archive_digest = "a" * 64
    selected = {
        "pullRequest": 123,
        "sourceArchiveEvidence": {
            "archiveDigest": archive_digest,
            "caseEvidenceDigest": "b" * 64,
            "pullResponseSha256": sha256_json(pull),
            "selectedCommentResponseSha256": {
                "456": sha256_json(comment),
            },
            "submittedReviewResponseSha256": {
                "789": sha256_json(review),
            },
        },
    }

    assert _validated_source_archive_evidence(
        selected,
        source_archive_digest=archive_digest,
        pull=pull,
        comments_by_id={456: comment},
        reviews_by_id={789: review},
        paper_ready=True,
    ) == selected["sourceArchiveEvidence"]

    changed_comment = dict(comment)
    changed_comment["path"] = "app/code/Magento/Other.php"
    with pytest.raises(ValueError, match="drifted from the source archive"):
        _validated_source_archive_evidence(
            selected,
            source_archive_digest=archive_digest,
            pull=pull,
            comments_by_id={456: changed_comment},
            reviews_by_id={789: review},
            paper_ready=True,
        )
