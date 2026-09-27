"""Prepare, coalesce, and open immutable proposed-tree graph generations."""
from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .exact_index import ExactIndexPreconditionError, RepositoryDeltaRebuildRequired
from .source_tree import attest_repository_source_tree, require_repository_source_tree_unchanged
from .review_snapshot import (
    ProposedTreeUnavailableError, ReviewOverlay, ProposedTreeGeneration,
    ProposedTreeReadSession, load_review_overlay, materialize_proposed_tree,
    _review_identity, _REVIEW_GRAPH_COMPOSITION, _canonical_json,
)


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

        overlay = load_review_overlay(str(arguments["review_overlay_path"]))
        representation = self.index_manager.current_representation_identity()
        key = (
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
            }),
            str(arguments.get("base_collection_target") or ""),
            str(arguments.get("base_generation_manifest_sha256") or ""),
            overlay.fingerprint,
            str(representation["representation_identity"]),
        )
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
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
    ) -> ProposedTreeGeneration:
        target_root = Path(target_repo_path).resolve()
        overlay = load_review_overlay(review_overlay_path)
        representation = self.index_manager.current_representation_identity()
        target_source = None
        base_receipt = None
        if base_collection_target and base_generation_manifest_sha256:
            base_receipt = self.index_manager.get_revision_preflight(
                workspace, project, target_branch, base_revision,
                collection_target=base_collection_target,
            )
            if (
                base_receipt is None
                or base_receipt.get("generation_manifest_sha256")
                != base_generation_manifest_sha256
                or base_receipt.get("index_representation_fingerprint")
                != representation["index_representation_fingerprint"]
            ):
                base_receipt = None
        if base_receipt is not None:
            target_source_tree_sha256 = str(
                base_receipt.get("source_tree_sha256") or ""
            )
            with self.index_manager.open_reader(
                workspace=workspace,
                project=project,
                branch=target_branch,
                revision=base_revision,
                generation_manifest_sha256=base_generation_manifest_sha256,
                collection_target=base_collection_target,
            ) as sealed_base_reader:
                base_repository_facts = sealed_base_reader.repository_facts()
            if base_repository_facts.get("revision") != base_revision:
                base_receipt = None
        if base_receipt is not None:
            project_type = base_repository_facts.get("projectType") or None
            source_root = base_repository_facts.get("sourceRoot") or None
            # A delta inherits the sealed base's selection and repository profile.
            include_patterns = tuple(
                base_receipt.get("index_include_patterns") or ()
            ) or None
            exclude_patterns = tuple(
                base_receipt.get("index_exclude_patterns") or ()
            ) or None
        else:
            base_collection_target = None
            base_generation_manifest_sha256 = None
            target_source = attest_repository_source_tree(
                target_root, base_revision,
            )
            target_source_tree_sha256 = target_source.tree_sha256
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
            materialize_proposed_tree(target_root, overlay, proposed_root)
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
            if not full_build:
                try:
                    self.index_manager.index_proposed_tree_delta(
                        repo_path=str(proposed_root),
                        workspace=workspace,
                        project=project,
                        branch=target_branch,
                        base_revision=base_revision,
                        commit=source_revision,
                        changed_paths=changed_paths,
                        deleted_paths=overlay.deleted_paths,
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
        # source files. ReviewSourceReader separately attests live source reads.
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

