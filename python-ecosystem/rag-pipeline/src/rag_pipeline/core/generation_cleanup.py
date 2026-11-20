"""Inventory of old Java-owned structural targets for registry reconciliation."""

import hashlib
import json
import re
import stat
import time

from .structural_store import STRUCTURAL_STORE_SCHEMA, StructuralGenerationStore

_BRANCH_TARGET = re.compile(
    r"cc_w[1-9][0-9]*_p([1-9][0-9]{0,18})_b([0-9a-f]{12})_r([0-9a-f]{12})_g[0-9a-f]{12}"
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ORPHAN_GRACE_SECONDS = 24 * 60 * 60


def orphan_candidates(store: StructuralGenerationStore) -> list[dict]:
    cutoff = time.time() - _ORPHAN_GRACE_SECONDS
    candidates = []
    for directory in store.generations_root.iterdir():
        try:
            directory_stat = directory.lstat()
            if (
                not _SHA256.fullmatch(directory.name)
                or not stat.S_ISDIR(directory_stat.st_mode)
                or directory_stat.st_mtime >= cutoff
            ):
                continue
            receipt_path = directory / "receipt.json"
            if not stat.S_ISREG(receipt_path.lstat().st_mode):
                continue
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict) or receipt.get("store_schema") != STRUCTURAL_STORE_SCHEMA:
                continue
            target = receipt.get("collection_target")
            match = _BRANCH_TARGET.fullmatch(target) if isinstance(target, str) else None
            if match is None or store.paths_for_target(target).directory != directory:
                continue
            project_id = int(match.group(1))
            if project_id > 2**63 - 1:
                continue
            snapshot = receipt.get("snapshot_metadata")
            if isinstance(snapshot, dict) and snapshot.get("kind") == "proposed_tree":
                continue
            workspace, project, branch, revision, manifest = (
                receipt.get(key)
                for key in ("workspace", "project", "branch", "repository_revision",
                            "generation_manifest_sha256")
            )
            if not all(isinstance(value, str) and value for value in (
                workspace, project, branch, revision, manifest,
            )):
                continue
            if (
                not _SHA256.fullmatch(manifest)
                or hashlib.sha256(branch.encode()).hexdigest()[:12] != match.group(2)
                or hashlib.sha256(revision.encode()).hexdigest()[:12] != match.group(3)
            ):
                continue
            candidates.append({
                "projectId": project_id,
                "workspace": workspace,
                "project": project,
                "branch": branch,
                "collectionTarget": target,
                "revision": revision,
                "manifestDigest": manifest,
            })
        except (FileNotFoundError, ValueError):
            continue
    return candidates
