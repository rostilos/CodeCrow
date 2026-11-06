"""Prepare, coalesce, and open immutable proposed-tree graph generations."""
from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .exact_index import ExactIndexPreconditionError, RepositoryDeltaRebuildRequired
from .generation_manifest import is_sha256_hex, canonical_index_selection_policy
from .source_tree import attest_repository_source_tree, require_repository_source_tree_unchanged
from .review_snapshot import (
    ProposedTreeUnavailableError, ReviewOverlay, ProposedTreeGeneration,
    ProposedTreeReadSession, load_review_overlay, materialize_proposed_tree,
    _review_identity, _REVIEW_GRAPH_COMPOSITION, _canonical_json,
)


logger = logging.getLogger(__name__)


def _index_policy(
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
    project_type: str | None,
    source_root: str | None,
) -> dict[str, Any]:
    for patterns in (include_patterns, exclude_patterns):
        if patterns is not None and (not isinstance(patterns, Sequence) or isinstance(patterns, (str, bytes))):
            raise ValueError("repository index patterns must be arrays")
    if any(value is not None and not isinstance(value, str) for value in (project_type, source_root)):
        raise ValueError("repository profile and source root must be strings")
    selection = canonical_index_selection_policy(include_patterns, exclude_patterns)
    profile = str(project_type or "").strip().casefold()
    root = str(source_root or "").strip().replace("\\", "/")
    return {
        "include_patterns": selection["includePatterns"],
        "exclude_patterns": selection["excludePatterns"],
        "project_type": profile if profile and profile != "auto" else None,
        "source_root": root if root and root != "." else None,
    }


@dataclass
class _PreparationFlight:
    """One in-process proposed-tree build shared by concurrent HTTP workers."""

    completed: threading.Event
    result: ProposedTreeGeneration | None = None
    error: BaseException | None = None



class ReviewGenerationService:
    """Own graph build lifecycle and receipt binding, independently of queries."""

    _preparation_lock = threading.Lock()
    _preparation_flights: dict[tuple[str, ...], _PreparationFlight] = {}

    def __init__(self, index_manager):
        self.index_manager = index_manager


    def preparation_key(self, **arguments: Any) -> tuple[str, ...]:
        """Identity shared by local and parent-process preparation coalescing."""
        overlay = load_review_overlay(str(arguments["review_overlay_path"]))
        representation = self.index_manager.current_representation_identity()
        return (
            str(id(self.index_manager)),
            str(arguments["workspace"]),
            str(arguments["project"]),
            str(arguments["target_branch"]),
            str(arguments["base_revision"]),
            str(arguments["source_revision"]),
            str(Path(arguments["target_repo_path"]).resolve()),
            _canonical_json({
                "include_patterns": arguments.get("include_patterns"),
                "exclude_patterns": arguments.get("exclude_patterns"),
                "project_type": arguments.get("project_type"),
                "source_root": arguments.get("source_root"),
                "index_policy": arguments.get("index_policy"),
                "base_generation_candidates": arguments.get("base_generation_candidates"),
            }),
            str(arguments.get("base_collection_target") or ""),
            str(arguments.get("base_generation_revision") or ""),
            str(arguments.get("base_generation_manifest_sha256") or ""),
            overlay.fingerprint,
            str(representation["representation_identity"]),
        )


    def prepare_generation_singleflight(
        self,
        prepare: Any = None,
        **arguments: Any,
    ) -> ProposedTreeGeneration:
        """Build one exact review generation and share it with concurrent callers.

        Stage 1 prepares before batch fan-out, but duplicate delivery and parallel
        review requests can still target the same immutable overlay. Followers
        wait for the leader's result instead of entering index mutation and
        receiving a 409 conflict.
        """

        key = self.preparation_key(**arguments)
        with self._preparation_lock:
            flight = self._preparation_flights.get(key)
            leader = flight is None
            if flight is None:
                flight = _PreparationFlight(completed=threading.Event())
                self._preparation_flights[key] = flight

        if not leader:
            try:
                wait_seconds = max(
                    30.0,
                    float(os.environ.get(
                        "RAG_REVIEW_PREPARATION_WAIT_SECONDS",
                        "900",
                    )),
                )
            except (TypeError, ValueError):
                wait_seconds = 900.0
            if not flight.completed.wait(wait_seconds):
                raise ProposedTreeUnavailableError(
                    "timed out waiting for the shared proposed-tree generation"
                )
            if flight.error is not None:
                raise ProposedTreeUnavailableError(
                    "shared proposed-tree generation failed: "
                    f"{type(flight.error).__name__}: {flight.error}"
                ) from flight.error
            if flight.result is None:
                raise ProposedTreeUnavailableError(
                    "shared proposed-tree generation completed without a receipt"
                )
            return replace(flight.result, cache_hit=True)

        try:
            result = (prepare or self.prepare_generation)(**arguments)
            flight.result = result
            return result
        except BaseException as error:
            flight.error = error
            raise
        finally:
            flight.completed.set()
            with self._preparation_lock:
                self._preparation_flights.pop(key, None)


    def prepare_generation(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        base_generation_revision: str | None = None,
        base_generation_candidates: Sequence[Mapping[str, Any]] | None = None,
        index_policy: Mapping[str, Any] | None = None,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
        cancellation_event: Any = None,
    ) -> ProposedTreeGeneration:
        if cancellation_event is not None:
            self.index_manager._raise_if_cancelled(cancellation_event)
        cancellation = {"cancellation_event": cancellation_event} if cancellation_event is not None else {}
        target_root = Path(target_repo_path).resolve()
        overlay = load_review_overlay(review_overlay_path)
        representation = self.index_manager.current_representation_identity()
        target_source = None
        base_receipt = None
        base_repository_facts: dict[str, Any] = {}
        requested_policy = None
        if index_policy is not None:
            try:
                if not isinstance(index_policy, Mapping):
                    raise ValueError("repository index policy must be an object")
                requested_policy = _index_policy(
                    index_policy.get("include_patterns"), index_policy.get("exclude_patterns"),
                    index_policy.get("project_type"), index_policy.get("source_root"),
                )
            except (TypeError, ValueError):
                logger.warning("Ignoring malformed optional repository index policy")
        if requested_policy is not None:
            include_patterns = requested_policy["include_patterns"]
            exclude_patterns = requested_policy["exclude_patterns"]
            project_type = requested_policy["project_type"]
            source_root = requested_policy["source_root"]
        seed_revision = base_generation_revision or base_revision
        candidates = []
        if base_collection_target and base_generation_manifest_sha256:
            candidates.append({
                "collection_target": base_collection_target,
                "generation_manifest_sha256": base_generation_manifest_sha256,
                "revision": seed_revision,
            })
        if isinstance(base_generation_candidates, (list, tuple)):
            candidates.extend(base_generation_candidates)
        elif base_generation_candidates is not None:
            logger.warning("Ignoring malformed optional repository generation candidates")
        seen_candidates = set()
        base_unavailable_reason = "no sealed base receipt supplied"
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                logger.warning("Ignoring malformed optional repository generation candidate")
                continue
            coordinates = tuple(str(candidate.get(field) or "") for field in (
                "collection_target", "generation_manifest_sha256", "revision",
            ))
            if not all(coordinates):
                logger.warning("Ignoring incomplete optional repository generation candidate")
                continue
            if coordinates in seen_candidates:
                continue
            seen_candidates.add(coordinates)
            collection, manifest, revision = coordinates
            try:
                receipt = self.index_manager.get_revision_preflight(
                    workspace, project, target_branch, revision,
                    collection_target=collection,
                )
                reason = None
                if receipt is None:
                    reason = "base generation unavailable for the requested tenant, branch and revision"
                elif receipt.get("generation_manifest_sha256") != manifest:
                    reason = "base generation manifest differs from the supplied receipt"
                elif receipt.get("index_representation_fingerprint") != representation["index_representation_fingerprint"]:
                    reason = "base generation uses a different structural representation"
                if reason is None:
                    with self.index_manager.open_reader(
                        workspace=workspace, project=project, branch=target_branch,
                        revision=revision, generation_manifest_sha256=manifest,
                        collection_target=collection,
                    ) as sealed_base_reader:
                        facts = sealed_base_reader.repository_facts()
                    if facts.get("revision") != revision:
                        reason = "base repository facts refer to a different revision"
                    elif revision != base_revision and (
                        not isinstance(facts.get("fileSha256"), Mapping)
                        or any(not is_sha256_hex(facts["fileSha256"].get(path))
                               for path in facts.get("paths") or [])
                    ):
                        reason = "different-revision seed lacks complete sealed file identities"
                    elif requested_policy is not None and requested_policy != _index_policy(
                        receipt.get("index_include_patterns"), receipt.get("index_exclude_patterns"),
                        facts.get("projectType"), facts.get("sourceRoot"),
                    ):
                        reason = "base generation uses a different project selection or profile"
                if reason is not None:
                    base_unavailable_reason = reason
                    logger.info(
                        "Proposed-tree seed skipped: workspace=%s project=%s branch=%s "
                        "collection=%s revision=%s reason=%s",
                        workspace, project, target_branch, collection, revision, reason,
                    )
                    continue
                base_receipt, base_repository_facts = receipt, facts
                base_collection_target, base_generation_manifest_sha256 = collection, manifest
                seed_revision = revision
                break
            except (ExactIndexPreconditionError, OSError, ValueError, sqlite3.DatabaseError) as unavailable:
                base_unavailable_reason = f"base generation could not be read: {unavailable}"
                logger.info(
                    "Proposed-tree seed skipped: workspace=%s project=%s branch=%s "
                    "collection=%s revision=%s reason=%s",
                    workspace, project, target_branch, collection, revision, base_unavailable_reason,
                )
        if base_receipt is not None:
            project_type = base_repository_facts.get("projectType") or None
            source_root = base_repository_facts.get("sourceRoot") or None
            # The seed's selection/profile is preserved. Its revision need not
            # equal the review target: the complete content delta below removes
            # every seed-only file and reparses every changed selected file.
            include_patterns = tuple(base_receipt.get("index_include_patterns") or ()) or None
            exclude_patterns = tuple(base_receipt.get("index_exclude_patterns") or ()) or None
            if seed_revision == base_revision:
                target_source_tree_sha256 = str(base_receipt.get("source_tree_sha256") or "")
            else:
                target_source = attest_repository_source_tree(target_root, base_revision)
                target_source_tree_sha256 = target_source.tree_sha256
        else:
            logger.info(
                "Proposed-tree base reuse unavailable: workspace=%s project=%s branch=%s "
                "base_revision=%s reason=%s; using exact full-tree fallback or cached generation",
                workspace, project, target_branch, base_revision, base_unavailable_reason,
            )
            base_collection_target = None
            base_generation_manifest_sha256 = None
            target_source = attest_repository_source_tree(target_root, base_revision)
            target_source_tree_sha256 = target_source.tree_sha256
        base_generation_revision = seed_revision if base_receipt is not None else None
        selected_overlay_paths = tuple(
            path.as_posix()
            for path in self.index_manager.loader.iter_repository_files(
                overlay.files_root,
                list(include_patterns) if include_patterns else None,
                list(exclude_patterns) if exclude_patterns else None,
                expected_file_sha256=overlay.file_sha256_by_path,
            )
        )
        collection_target, _ = _review_identity(
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            source_revision=source_revision,
            target_source_tree_sha256=target_source_tree_sha256,
            overlay_sha256=overlay.fingerprint,
            representation=representation,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        )
        snapshot_metadata = {
            "kind": "proposed_tree",
            "storage_mode": _REVIEW_GRAPH_COMPOSITION,
            "base_revision": base_revision,
            "base_collection_target": base_collection_target,
            "base_generation_revision": base_generation_revision,
            "base_generation_manifest_sha256": (
                base_generation_manifest_sha256
            ),
            "base_index_selection_policy_sha256": (
                base_receipt.get("index_selection_policy_sha256")
                if base_receipt is not None else None
            ),
            "source_revision": source_revision,
            "target_source_tree_sha256": target_source_tree_sha256,
            "overlay_sha256": overlay.fingerprint,
            "changed_paths": list(overlay.changed_paths),
            "deleted_paths": list(overlay.deleted_paths),
            "selected_overlay_paths": list(selected_overlay_paths),
            "project_type": project_type,
            "source_root": source_root,
            "representation_identity": representation["representation_identity"],
        }
        receipt = self.index_manager.get_revision_preflight(
            workspace,
            project,
            target_branch,
            source_revision,
            collection_target=collection_target,
        )
        if receipt is not None:
            if receipt.get("snapshot_metadata") != snapshot_metadata:
                raise ExactIndexPreconditionError(
                    "cached proposed-tree generation has incompatible provenance"
                )
            return self._generation(
                receipt,
                collection_target,
                target_source_tree_sha256,
                overlay,
                representation,
                cache_hit=True,
            )

        if target_source is None:
            target_source = attest_repository_source_tree(target_root, base_revision)
        if target_source_tree_sha256 != target_source.tree_sha256:
            raise ExactIndexPreconditionError(
                "sealed target-head generation does not match the supplied "
                "repository archive"
            )
        with tempfile.TemporaryDirectory(prefix="codecrow-review-tree-") as temporary:
            proposed_root = Path(temporary) / "repository"
            if cancellation_event is not None:
                self.index_manager._raise_if_cancelled(cancellation_event)
            materialize_proposed_tree(target_root, overlay, proposed_root)
            if cancellation_event is not None:
                self.index_manager._raise_if_cancelled(cancellation_event)
            require_repository_source_tree_unchanged(target_root, target_source)
            reloaded_overlay = load_review_overlay(review_overlay_path)
            if reloaded_overlay.fingerprint != overlay.fingerprint:
                raise ProposedTreeUnavailableError(
                    "review overlay changed while the proposed tree was materialized"
                )
            deleted_path_set = set(overlay.deleted_paths)
            changed_paths = tuple(
                path
                for path in overlay.changed_paths
                if path not in deleted_path_set
            )
            full_build = base_receipt is None
            delta_changed_paths = changed_paths
            delta_deleted_paths = overlay.deleted_paths
            if base_receipt is not None and seed_revision != base_revision:
                proposed_hashes = dict(target_source.file_sha256_by_path)
                for path in overlay.deleted_paths:
                    proposed_hashes.pop(path, None)
                proposed_hashes.update(overlay.file_sha256_by_path)
                selected_paths = {
                    path.as_posix() for path in self.index_manager.loader.iter_repository_files(
                        proposed_root,
                        list(include_patterns) if include_patterns else None,
                        list(exclude_patterns) if exclude_patterns else None,
                        expected_file_sha256=proposed_hashes,
                    )
                }
                seed_paths = set(base_repository_facts["paths"])
                seed_hashes = base_repository_facts["fileSha256"]
                delta_changed_paths = tuple(sorted(
                    path for path in selected_paths
                    if seed_hashes.get(path) != proposed_hashes[path]
                ))
                delta_deleted_paths = tuple(sorted(seed_paths - selected_paths))
                logger.info(
                    "Reusing sealed graph seed: workspace=%s project=%s seed_revision=%s "
                    "target_revision=%s changed_files=%d deleted_files=%d",
                    workspace, project, seed_revision, base_revision,
                    len(delta_changed_paths), len(delta_deleted_paths),
                )
            if not full_build:
                try:
                    self.index_manager.index_proposed_tree_delta(
                        repo_path=str(proposed_root),
                        workspace=workspace,
                        project=project,
                        branch=target_branch,
                        base_revision=seed_revision,
                        commit=source_revision,
                        changed_paths=delta_changed_paths,
                        deleted_paths=delta_deleted_paths,
                        # The manager attests this exclusively-owned tree.
                        source_tree_sha256=None,
                        collection_target=collection_target,
                        base_collection_target=base_collection_target,
                        base_generation_manifest_sha256=(
                            base_generation_manifest_sha256
                        ),
                        project_type=project_type,
                        source_root=source_root,
                        snapshot_metadata=snapshot_metadata,
                        source_tree_exclusively_owned=True,
                        **cancellation,
                    )
                except RepositoryDeltaRebuildRequired:
                    full_build = True
            if full_build:
                # A cold review or an incompatible delta indexes the same exact
                # proposed tree directly, without first indexing the target head.
                self.index_manager.index_repository(
                    repo_path=str(proposed_root),
                    workspace=workspace,
                    project=project,
                    branch=target_branch,
                    commit=source_revision,
                    include_patterns=(
                        list(include_patterns) if include_patterns else None
                    ),
                    exclude_patterns=(
                        list(exclude_patterns) if exclude_patterns else None
                    ),
                    collection_target=collection_target,
                    project_type=project_type,
                    source_root=source_root,
                    snapshot_metadata=snapshot_metadata,
                    source_tree_exclusively_owned=True,
                    **cancellation,
                )
        receipt = self.index_manager.get_revision_preflight(
            workspace,
            project,
            target_branch,
            source_revision,
            collection_target=collection_target,
        )
        if receipt is None or receipt.get("snapshot_metadata") != snapshot_metadata:
            raise ExactIndexPreconditionError(
                "proposed-tree structural generation was not sealed exactly"
            )
        return self._generation(
            receipt,
            collection_target,
            target_source_tree_sha256,
            overlay,
            representation,
            cache_hit=False,
        )


    @staticmethod
    def _generation(
        receipt: Mapping[str, Any],
        collection_target: str,
        target_source_tree_sha256: str,
        overlay: ReviewOverlay,
        representation: Mapping[str, Any],
        *,
        cache_hit: bool,
    ) -> ProposedTreeGeneration:
        return ProposedTreeGeneration(
            collection_target=collection_target,
            receipt=dict(receipt),
            target_source_tree_sha256=target_source_tree_sha256,
            overlay_sha256=overlay.fingerprint,
            proposed_source_tree_sha256=str(receipt["source_tree_sha256"]),
            representation_identity=str(representation["representation_identity"]),
            changed_paths=overlay.changed_paths,
            deleted_paths=overlay.deleted_paths,
            cache_hit=cache_hit,
        )


    def load_prepared_generation(
        self,
        *,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        base_collection_target: str | None,
        base_generation_manifest_sha256: str | None,
        base_generation_revision: str | None = None,
        review_collection_target: str | None,
        review_generation_manifest_sha256: str | None,
    ) -> ProposedTreeGeneration:
        """Verify and load a sealed review generation without mutating storage."""

        if not review_collection_target or not review_generation_manifest_sha256:
            raise ExactIndexPreconditionError(
                "proposed-tree query requires a sealed review-generation receipt"
            )
        representation = self.index_manager.current_representation_identity()
        receipt = self.index_manager.get_revision_preflight(
            workspace,
            project,
            target_branch,
            source_revision,
            collection_target=review_collection_target,
        )
        if receipt is None:
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation is unavailable"
            )
        if receipt.get("generation_manifest_sha256") != (
            review_generation_manifest_sha256
        ):
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation receipt does not match"
            )
        if receipt.get("index_representation_fingerprint") != (
            representation["index_representation_fingerprint"]
        ):
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation uses a different "
                "structural representation"
            )
        metadata = receipt.get("snapshot_metadata")
        if not isinstance(metadata, Mapping):
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation has no provenance"
            )
        expected_metadata = {
            "kind": "proposed_tree",
            "storage_mode": _REVIEW_GRAPH_COMPOSITION,
            "base_revision": base_revision,
            "base_collection_target": base_collection_target,
            "base_generation_manifest_sha256": (
                base_generation_manifest_sha256
            ),
            "source_revision": source_revision,
            "representation_identity": (
                representation["representation_identity"]
            ),
        }
        if base_generation_revision is not None:
            expected_metadata["base_generation_revision"] = base_generation_revision
        mismatches = sorted(
            key
            for key, expected in expected_metadata.items()
            if metadata.get(key) != expected
        )
        if mismatches:
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation has incompatible "
                "provenance: " + ", ".join(mismatches)
            )
        target_source_tree_sha256 = str(
            metadata.get("target_source_tree_sha256") or ""
        )
        if not target_source_tree_sha256:
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation lacks target source identity"
            )
        # The receipt binds immutable graph bytes to tenant, revision and overlay
        # provenance. Graph-only reads do not depend on the lifetime of temporary
        # source files. Exact live source access remains owned by the VCS MCP tools.
        return ProposedTreeGeneration(
            collection_target=review_collection_target,
            receipt=dict(receipt),
            target_source_tree_sha256=target_source_tree_sha256,
            overlay_sha256=str(metadata["overlay_sha256"]),
            proposed_source_tree_sha256=str(receipt["source_tree_sha256"]),
            representation_identity=str(metadata["representation_identity"]),
            changed_paths=tuple(metadata.get("changed_paths") or ()),
            deleted_paths=tuple(metadata.get("deleted_paths") or ()),
            cache_hit=True,
        )


    @contextmanager
    def open_read_session(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        base_generation_revision: str | None = None,
        review_collection_target: str | None = None,
        review_generation_manifest_sha256: str | None = None,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
    ) -> Iterator[ProposedTreeReadSession]:
        """Open the complete exact proposed-tree generation.

        Keeping generation preparation and the physical reader inside this
        context manager prevents a later operation from accidentally reopening
        the target-head generation or observing an already-closed generation.
        """

        generation = self.load_prepared_generation(
            review_overlay_path=review_overlay_path,
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            base_collection_target=base_collection_target,
            base_generation_revision=base_generation_revision,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
        )
        with self.index_manager.open_reader(
            workspace=workspace,
            project=project,
            branch=target_branch,
            revision=source_revision,
            generation_manifest_sha256=str(
                generation.receipt["generation_manifest_sha256"]
            ),
            collection_target=generation.collection_target,
        ) as proposed_reader:
            yield ProposedTreeReadSession(
                reader=proposed_reader,
                generation=generation,
            )
