from __future__ import annotations

import json
import subprocess

import pytest

from tools.review_quality.isolated_paired_quality_capture import (
    load_local_capture_case,
)
from tools.review_quality.neutral_corpus import (
    CASE_DEFINITIONS,
    FIXTURE_PATH,
    _load_case_definitions,
    definition_digest,
    materialize_corpus,
)


def _git(repository, *arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def test_materializes_four_remote_free_candidate_blind_profiles(tmp_path):
    report = materialize_corpus(tmp_path / "corpus")

    assert report["status"] == "drafts-created"
    assert report["providerCalls"] == 0
    assert report["embeddingCalls"] == 0
    assert report["connectedRepositories"] == 0
    assert report["candidateOutputsInspected"] is False
    assert {tuple(case["languages"]) for case in report["cases"]} == {
        ("python",),
        ("java",),
        ("typescript",),
        ("java", "python", "typescript"),
    }

    for item in report["cases"]:
        repository = tmp_path / "corpus" / item["caseId"] / "repository"
        assert _git(repository, "remote") == ""
        assert _git(repository, "rev-parse", "HEAD") == item["headCommit"]
        inventory = json.loads(
            (
                tmp_path
                / "corpus"
                / item["caseId"]
                / "ground-truth-draft.json"
            ).read_text(encoding="utf-8")
        )
        assert inventory["status"] == "draft-pending-independent-certification"
        assert inventory["candidateOutputsHiddenDuringDefectInventory"] is True
        assert inventory["expectedDefects"]
        assert {
            defect["file"] for defect in inventory["expectedDefects"]
        }.issubset(set(item["changedFiles"]))


def test_materialization_is_byte_and_revision_deterministic(tmp_path):
    first = materialize_corpus(tmp_path / "first")
    second = materialize_corpus(tmp_path / "second")

    first_projection = [
        {
            key: case[key]
            for key in (
                "caseId",
                "baseCommit",
                "headCommit",
                "rawDiffSha256",
                "changedFiles",
                "definitionDigest",
            )
        }
        for case in first["cases"]
    ]
    second_projection = [
        {
            key: case[key]
            for key in (
                "caseId",
                "baseCommit",
                "headCommit",
                "rawDiffSha256",
                "changedFiles",
                "definitionDigest",
            )
        }
        for case in second["cases"]
    ]

    assert first_projection == second_projection


@pytest.mark.parametrize("case_id", tuple(CASE_DEFINITIONS))
def test_every_materialized_case_passes_capture_source_validation(
    tmp_path,
    case_id,
):
    report = materialize_corpus(
        tmp_path / "corpus",
        case_ids=(case_id,),
    )
    item = report["cases"][0]

    loaded = load_local_capture_case(
        tmp_path / "corpus" / case_id / "case.json",
        temporary_root=tmp_path / "loaded",
        maximum_files=20,
        maximum_changed_lines=2_000,
        maximum_repository_files=2_000,
        maximum_repository_bytes=20_000_000,
    )

    assert loaded.case_id == case_id
    assert loaded.languages == tuple(item["languages"])
    assert loaded.candidate_plugins == tuple(item["candidatePlugins"])
    assert loaded.request_plugins == tuple(item["requestPlugins"])
    assert loaded.repository.base_revision == item["baseCommit"]
    assert loaded.repository.head_revision == item["headCommit"]
    assert loaded.repository.changed_files == tuple(item["changedFiles"])


def test_definitions_have_stable_nonempty_digests_and_evidence():
    digests = {
        case_id: definition_digest(definition)
        for case_id, definition in CASE_DEFINITIONS.items()
    }

    assert len(set(digests.values())) == len(CASE_DEFINITIONS)
    assert all(len(value) == 64 for value in digests.values())
    for definition in CASE_DEFINITIONS.values():
        changed_files = set(definition.head_replacements)
        for defect in definition.expected_defects:
            assert defect.file in changed_files
            assert defect.evidence_files
            assert set(defect.evidence_files).issubset(definition.base_files)


def test_durable_fixture_is_plugin_owned_and_strictly_loaded(tmp_path):
    assert FIXTURE_PATH.parts[-4:] == (
        "analysis-plugins",
        "fixtures",
        "review-quality",
        "neutral-corpus.json",
    )
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["candidatePlugins"] = ["python", "alpha"]
    malformed = tmp_path / "neutral-corpus.json"
    malformed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unique sorted strings"):
        _load_case_definitions(malformed)


def test_rejects_unknown_duplicate_and_nonempty_output(tmp_path):
    with pytest.raises(ValueError, match="unknown neutral corpus"):
        materialize_corpus(tmp_path / "unknown", case_ids=("unknown",))

    case_id = next(iter(CASE_DEFINITIONS))
    with pytest.raises(ValueError, match="must not contain duplicates"):
        materialize_corpus(
            tmp_path / "duplicate",
            case_ids=(case_id, case_id),
        )

    output = tmp_path / "occupied"
    output.mkdir()
    (output / "keep.txt").write_text("owned by operator\n", encoding="utf-8")
    with pytest.raises(ValueError, match="absent or empty"):
        materialize_corpus(output)
