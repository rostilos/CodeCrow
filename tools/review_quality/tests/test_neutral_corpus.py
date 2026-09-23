from __future__ import annotations

import json
import subprocess

import pytest

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
def test_every_materialized_case_has_exact_local_manifest(
    tmp_path,
    case_id,
):
    report = materialize_corpus(
        tmp_path / "corpus",
        case_ids=(case_id,),
    )
    item = report["cases"][0]
    repository = tmp_path / "corpus" / case_id / "repository"
    manifest = json.loads((
        tmp_path / "corpus" / case_id / "case.json"
    ).read_text(encoding="utf-8"))

    assert manifest["caseId"] == case_id
    assert manifest["repositoryPath"] == str(repository.resolve())
    assert manifest["languages"] == item["languages"]
    assert manifest["candidatePlugins"] == item["candidatePlugins"]
    assert manifest["requestPlugins"] == item["requestPlugins"]
    assert manifest["baseCommit"] == item["baseCommit"]
    assert manifest["headCommit"] == item["headCommit"]
    assert _git(repository, "rev-parse", "HEAD") == item["headCommit"]
    assert _git(
        repository, "diff", "--name-only",
        item["baseCommit"], item["headCommit"],
    ).splitlines() == item["changedFiles"]


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
