from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from magento2_benchmark.repository_evidence import (
    create_repository_evidence,
    validate_repository_evidence,
)
from magento2_benchmark.runner import _git_diff, _git_paths
from magento2_benchmark.util import sha256_json

from conftest import write_json


def _commit(repository: Path, message: str) -> str:
    subprocess.run(
        ["git", "-C", str(repository), "add", "."],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", message],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _source_fixture(
    tmp_path: Path,
    *,
    suffix: str = "one",
) -> tuple[Path, dict[str, Any]]:
    repository = tmp_path / f"source-{suffix}"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "benchmark@example.test",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.name",
            "Benchmark Fixture",
        ],
        check=True,
    )
    for name in ("A.php", "B.php", "C.php"):
        (repository / name).write_text(
            f"<?php\nreturn 'base-{suffix}';\n",
            encoding="utf-8",
        )
    base = _commit(repository, "base")
    for name in ("A.php", "B.php", "C.php"):
        (repository / name).write_text(
            f"<?php\nreturn 'reviewed-{suffix}';\n",
            encoding="utf-8",
        )
    reviewed = _commit(repository, "reviewed")
    (repository / "A.php").write_text(
        f"<?php\nreturn 'fix-{suffix}';\n",
        encoding="utf-8",
    )
    fix = _commit(repository, "fix")
    (repository / "B.php").write_text(
        f"<?php\nreturn 'final-{suffix}';\n",
        encoding="utf-8",
    )
    final = _commit(repository, "final")
    reviewed_paths = _git_paths(repository, base, reviewed)
    corpus = {
        "corpusId": f"repository-evidence-{suffix}",
        "corpusDigest": sha256_json({"fixture": suffix}),
        "repository": "magento/magento2",
        "cases": [
            {
                "caseId": "m2b-001",
                "snapshot": {
                    "baseSha": base,
                    "headSha": reviewed,
                    "fileCount": len(reviewed_paths),
                    "changedPaths": reviewed_paths,
                    "diffSha256": sha256_json(
                        # Replaced below with the text digest to keep this
                        # fixture independent from corpus construction helpers.
                        {"unused": True}
                    ),
                },
                "sourcePr": {"finalHeadSha": final},
                "goldenComments": [
                    {"validity": {"fixCommitSha": fix}}
                ],
            }
        ],
    }
    from magento2_benchmark.util import sha256_text

    corpus["cases"][0]["snapshot"]["diffSha256"] = sha256_text(
        _git_diff(repository, base, reviewed)
    )
    return repository, corpus


def test_repository_evidence_is_portable_and_reconstructs_b_h_f_fix(
    tmp_path,
):
    source, corpus = _source_fixture(tmp_path)
    corpus_path = write_json(tmp_path / "corpus.json", corpus)
    output_root = tmp_path / "repository-evidence"

    manifest = create_repository_evidence(
        corpus_path=corpus_path,
        source_repository=source,
        output_root=output_root,
    )
    summary, repository = validate_repository_evidence(
        manifest_path=output_root / "repository-evidence.json",
        corpus=corpus,
        evidence_root=output_root,
    )

    assert repository == output_root / "repository.git"
    assert manifest["repositoryPath"] == "repository.git"
    assert summary["requiredCommits"] == 4
    assert summary["cases"] == 1
    serialized = json.dumps(manifest)
    assert str(source) not in serialized
    config = (repository / "config").read_text(encoding="utf-8")
    assert str(source) not in config
    assert "remote " not in config.casefold()


def test_repository_evidence_rejects_missing_and_swapped_store(tmp_path):
    source, corpus = _source_fixture(tmp_path, suffix="one")
    corpus_path = write_json(tmp_path / "corpus-one.json", corpus)
    output_root = tmp_path / "evidence-one"
    create_repository_evidence(
        corpus_path=corpus_path,
        source_repository=source,
        output_root=output_root,
    )

    missing_root = tmp_path / "missing-root"
    missing_root.mkdir()
    shutil.copy2(
        output_root / "repository-evidence.json",
        missing_root / "repository-evidence.json",
    )
    with pytest.raises(ValueError, match="escapes or is missing"):
        validate_repository_evidence(
            manifest_path=missing_root / "repository-evidence.json",
            corpus=corpus,
            evidence_root=missing_root,
        )

    other_source, other_corpus = _source_fixture(tmp_path, suffix="two")
    other_root = tmp_path / "evidence-two"
    create_repository_evidence(
        corpus_path=write_json(
            tmp_path / "corpus-two.json",
            other_corpus,
        ),
        source_repository=other_source,
        output_root=other_root,
    )
    swapped = tmp_path / "swapped"
    swapped.mkdir()
    shutil.copytree(
        other_root / "repository.git",
        swapped / "repository.git",
    )
    swapped_manifest = copy.deepcopy(
        json.loads(
            (output_root / "repository-evidence.json").read_text(
                encoding="utf-8"
            )
        )
    )
    swapped_manifest["evidenceDigest"] = sha256_json(
        {
            key: value
            for key, value in swapped_manifest.items()
            if key != "evidenceDigest"
        }
    )
    write_json(swapped / "repository-evidence.json", swapped_manifest)
    with pytest.raises(
        (RuntimeError, ValueError),
        match="required|commit|object|snapshot|command failed",
    ):
        validate_repository_evidence(
            manifest_path=swapped / "repository-evidence.json",
            corpus=corpus,
            evidence_root=swapped,
        )


def test_repository_evidence_rejects_unexpected_ref_and_symlink(tmp_path):
    source, corpus = _source_fixture(tmp_path)
    output_root = tmp_path / "evidence"
    create_repository_evidence(
        corpus_path=write_json(tmp_path / "corpus.json", corpus),
        source_repository=source,
        output_root=output_root,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(output_root / "repository.git"),
            "update-ref",
            "refs/heads/injected",
            corpus["cases"][0]["snapshot"]["headSha"],
        ],
        check=True,
    )
    with pytest.raises(ValueError, match="unexpected ref"):
        validate_repository_evidence(
            manifest_path=output_root / "repository-evidence.json",
            corpus=corpus,
            evidence_root=output_root,
        )

    link_root = tmp_path / "link-evidence"
    link_root.mkdir()
    (link_root / "repository.git").symlink_to(
        output_root / "repository.git",
        target_is_directory=True,
    )
    shutil.copy2(
        output_root / "repository-evidence.json",
        link_root / "repository-evidence.json",
    )
    with pytest.raises(ValueError, match="symlink"):
        validate_repository_evidence(
            manifest_path=link_root / "repository-evidence.json",
            corpus=corpus,
            evidence_root=link_root,
        )
