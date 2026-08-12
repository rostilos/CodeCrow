"""PR file indexing endpoints."""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from llama_index.core import Document as LlamaDocument
from qdrant_client.models import Filter, FieldCondition, MatchValue

from ..models import PRIndexRequest
from ...core.repository_overlay import (
    IncrementalIndexPreconditionError,
    build_overlay_capabilities,
    load_repository_snapshots,
)
from ...core.coordination import (
    MutationCoordinationUnavailable,
    MutationLeaseUnavailable,
)
from ...core.pr_overlay_identity import (
    ZERO_FINGERPRINT,
    is_complete_reusable_generation,
    pr_overlay_generation_fingerprint,
)
from ...core.review_grouping import review_groups_from_architecture_payloads
from ...core.index_representation import (
    INDEX_REPRESENTATION_PAYLOAD_KEY,
    observe_branch_representation,
)
from ...core.pr_overlay_representation import (
    PR_OVERLAY_REPRESENTATION_PAYLOAD_KEY,
)
from ...core.pr_overlay_manifest import (
    read_pr_overlay_generation,
)
from ...core.revision_binding import require_repository_generation

logger = logging.getLogger(__name__)
router = APIRouter(tags=["pr"])


def _get_index_manager():
    from ..api import index_manager
    return index_manager


def _content_state(file_info: object) -> str:
    state = getattr(file_info, "content_state", "complete")
    return state if state in {"complete", "partial_diff"} else "complete"


def _effective_detection_evidence(
    *,
    repository_plugins: tuple[str, ...],
    stored_plugin_ids: tuple[str, ...],
    requested_evidence: dict[str, list[str]],
    target_branch: str,
    stored_fingerprint: str | None,
) -> dict[str, tuple[str, ...]]:
    """Bind the effective plugin set to target-index and PR evidence."""
    indexed = set(stored_plugin_ids)
    result: dict[str, tuple[str, ...]] = {}
    for plugin_id in repository_plugins:
        evidence = set(requested_evidence.get(plugin_id, ()))
        if plugin_id in indexed:
            evidence.add(
                "indexed-target:"
                f"{target_branch}:"
                f"{stored_fingerprint or ZERO_FINGERPRINT}:"
                f"{plugin_id}"
            )
        if not evidence:
            raise RuntimeError(
                f"effective repository plugin {plugin_id} has no "
                "revision-bound selection evidence"
            )
        result[plugin_id] = tuple(sorted(evidence))
    return result


def _capabilities_payload(capabilities, implementation_fingerprint: str):
    if capabilities is None:
        return None
    return {
        "repositoryPlugins": list(capabilities.repository_plugins),
        "filePlugins": {
            path: list(plugin_ids)
            for path, plugin_ids in capabilities.file_plugins.items()
        },
        "detectionEvidence": {
            plugin_id: list(evidence)
            for plugin_id, evidence
            in capabilities.detection_evidence.items()
        },
        "unavailableCapabilities": list(
            capabilities.unavailable_capabilities
        ),
        "fingerprint": capabilities.fingerprint,
        "descriptorFingerprint": capabilities.descriptor_fingerprint,
        "implementationFingerprint": implementation_fingerprint,
    }


@router.post("/index/pr-files")
def index_pr_files(request: PRIndexRequest):
    """
    Index PR files into the main collection with PR-specific metadata.

    Files are indexed with metadata: pr=true, pr_number, pr_branch.
    This allows hybrid queries that prioritize PR data over branch data.
    An exact persisted generation is reused. A changed generation is prepared
    completely and then replaces the previous points with rollback protection.
    """
    index_manager = _get_index_manager()
    mutation_context = index_manager.pr_overlay_mutation(
        request.workspace,
        request.project,
        request.pr_number,
        "index-pr-overlay",
    )
    mutation_lease = None
    try:
        mutation_lease = mutation_context.__enter__()
        base_receipt = None
        target_branch = request.base_branch or request.branch
        requested_collection_target = getattr(
            request, "collection_target", None
        )
        requested_base_manifest = getattr(
            request, "base_generation_manifest_sha256", None
        )
        if not isinstance(requested_collection_target, str):
            requested_collection_target = None
        if not isinstance(requested_base_manifest, str):
            requested_base_manifest = None
        if requested_collection_target and not request.base_revision:
            raise IncrementalIndexPreconditionError(
                "PR overlay collection target requires an exact base revision"
            )
        if requested_base_manifest and not request.base_revision:
            raise IncrementalIndexPreconditionError(
                "PR overlay base generation receipt requires an exact base revision"
            )
        exact_binding = bool(
            request.base_revision
            and (requested_collection_target or requested_base_manifest)
        )
        if exact_binding:
            base_receipt = require_repository_generation(
                index_manager,
                workspace=request.workspace,
                project=request.project,
                branch=target_branch,
                revision=request.base_revision,
                generation_manifest_sha256=(
                    requested_base_manifest
                ),
                collection_target=requested_collection_target,
            )
        collection_name = (
            base_receipt["_collection_target"]
            if base_receipt
            else requested_collection_target
            or index_manager._get_project_collection_name(
                request.workspace, request.project
            )
        )
        base_generation_receipt = (
            {
                "base_generation_manifest_sha256": base_receipt[
                    "generation_manifest_sha256"
                ],
                "plugin_fingerprint": base_receipt["plugin_fingerprint"],
                "plugin_descriptor_fingerprint": base_receipt[
                    "plugin_descriptor_fingerprint"
                ],
                "plugin_implementation_fingerprint": base_receipt[
                    "plugin_implementation_fingerprint"
                ],
                "index_representation_fingerprint": base_receipt[
                    "index_representation_fingerprint"
                ],
            }
            if base_receipt else {}
        )

        index_manager._ensure_collection_exists(collection_name)

        # Keep the last complete PR generation until its replacement has been
        # fully parsed, embedded and validated.  Mutation happens once at the
        # end and the shared replacement primitive restores these records if
        # either upsert or stale-point deletion fails.
        old_pr_points = []
        offset = None
        while True:
            points, offset = index_manager.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[
                    FieldCondition(
                        key="pr_number",
                        match=MatchValue(value=request.pr_number),
                    )
                ]),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            old_pr_points.extend(points)
            if offset is None:
                break

        # Recover the exact plugin-owned repository state from the PR target
        # branch.  The host never interprets the snapshots; it only validates,
        # overlays changed artifacts, and asks the selected plugins to rebuild.
        representation_fingerprint = (
            index_manager.index_representation_fingerprint
        )
        overlay_representation_fingerprint = (
            index_manager.pr_overlay_representation_fingerprint
        )
        observe_branch_representation(
            index_manager.qdrant_client,
            collection_name,
            target_branch,
            expected_fingerprint=representation_fingerprint,
        )
        (
            snapshots,
            stored_plugin_ids,
            stored_fingerprint,
            _stored_descriptor_fingerprint,
            _stored_implementation_fingerprint,
        ) = load_repository_snapshots(
            index_manager.qdrant_client,
            collection_name,
            target_branch,
        )
        requested_plugin_ids = tuple(request.repository_plugins)
        if requested_plugin_ids:
            if (
                index_manager.plugin_catalog is None
                or index_manager.plugin_runtime is None
            ):
                raise RuntimeError("repository plugins are unavailable")
        if stored_plugin_ids:
            if (
                index_manager.plugin_catalog is None
                or index_manager.plugin_runtime is None
            ):
                raise RuntimeError("repository plugins are unavailable")
        # The PR request is selected from changed/enriched paths, while the
        # target-branch capability set is selected from the whole repository.
        # Therefore the PR set is normally a subset and its selection
        # fingerprint normally differs across revisions. The indexed target
        # remains authoritative for existing capabilities. A capability
        # introduced entirely by added PR files can start from empty state;
        # modified/deleted evidence still requires a target snapshot.
        missing_requested_plugins = tuple(
            plugin_id
            for plugin_id in requested_plugin_ids
            if plugin_id not in stored_plugin_ids
        )
        if requested_plugin_ids and index_manager.plugin_runtime is None:
            raise RuntimeError("repository plugins are unavailable")

        repository_plugins = tuple(
            descriptor.id
            for descriptor in index_manager.plugin_catalog.registry.resolve(
                (*stored_plugin_ids, *requested_plugin_ids)
            )
        )
        implementation_fingerprint = (
            index_manager.plugin_catalog.implementation_fingerprint(
                repository_plugins
            )
            if repository_plugins
            else "sha256:" + "0" * 64
        )
        fingerprint = (
            request.plugin_fingerprint
            if missing_requested_plugins
            else (stored_fingerprint or request.plugin_fingerprint)
        )
        capabilities = None
        required_snapshot_plugins: set[str] = set()
        fresh_repository_plugins: set[str] = set()
        if repository_plugins:
            if not request.source_revision:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "effective plugin capabilities require the immutable "
                        "PR source revision"
                    ),
                )
            effective_detection_evidence = _effective_detection_evidence(
                repository_plugins=repository_plugins,
                stored_plugin_ids=tuple(stored_plugin_ids),
                requested_evidence=request.plugin_detection_evidence,
                target_branch=target_branch,
                stored_fingerprint=stored_fingerprint,
            )
            capabilities = build_overlay_capabilities(
                index_manager.plugin_catalog.registry,
                repository_plugins,
                fingerprint,
                tuple(sorted(file_info.path for file_info in request.files)),
                revision=request.source_revision,
                detection_evidence=effective_detection_evidence,
            )
            required_snapshot_plugins = set(
                index_manager.plugin_runtime.repository_analysis_plugins(capabilities)
            )
            fresh_repository_plugins = (
                required_snapshot_plugins & set(missing_requested_plugins)
            )
            if fresh_repository_plugins:
                added_paths = {
                    file_info.path
                    for file_info in request.files
                    if file_info.change_type == "ADDED"
                }
                request_paths = {file_info.path for file_info in request.files}
                unsafe_fresh_plugins = []
                for plugin_id in sorted(fresh_repository_plugins):
                    evidence = request.plugin_detection_evidence.get(plugin_id, ())
                    evidence_paths = {
                        path
                        for path in request_paths
                        if any(
                            item == f"file:{path}"
                            or item.endswith(f":{path}")
                            or f":{path}:" in item
                            for item in evidence
                        )
                    }
                    if (
                        not evidence_paths
                        or not evidence_paths.issubset(added_paths)
                    ):
                        unsafe_fresh_plugins.append(plugin_id)
                if unsafe_fresh_plugins:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"target branch '{target_branch}' is indexed without "
                            "repository-analysis plugins "
                            f"({', '.join(unsafe_fresh_plugins)}) and the PR does "
                            "not prove they are introduced only by added files; "
                            f"reindex target branch '{target_branch}' before review"
                        ),
                    )
            available_snapshot_plugins = {
                snapshot.plugin_id for snapshot in snapshots
            }
            missing_snapshot_plugins = sorted(
                required_snapshot_plugins
                - available_snapshot_plugins
                - fresh_repository_plugins
            )
            if missing_snapshot_plugins:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"target branch '{target_branch}' is missing repository-analysis "
                        f"snapshots for {', '.join(missing_snapshot_plugins)}; reindex "
                        f"target branch '{target_branch}' before review"
                    ),
                )

        request_partial_files = tuple(sorted(
            file_info.path
            for file_info in request.files
            if (
                file_info.change_type != "DELETED"
                and _content_state(file_info) != "complete"
            )
        ))
        generation_fingerprint = None
        if request.source_revision and request.base_revision:
            generation_fingerprint = pr_overlay_generation_fingerprint(
                workspace=request.workspace,
                project=request.project,
                pr_number=request.pr_number,
                branch=request.branch,
                base_branch=target_branch,
                source_revision=request.source_revision,
                base_revision=request.base_revision,
                base_generation_manifest_sha256=(
                    base_receipt["generation_manifest_sha256"]
                    if base_receipt else ""
                ),
                files=request.files,
                requested_plugin_ids=requested_plugin_ids,
                repository_plugin_ids=repository_plugins,
                request_plugin_fingerprint=request.plugin_fingerprint,
                target_plugin_fingerprint=stored_fingerprint or ZERO_FINGERPRINT,
                capability_fingerprint=(
                    capabilities.fingerprint
                    if capabilities is not None
                    else ZERO_FINGERPRINT
                ),
                descriptor_fingerprint=(
                    capabilities.descriptor_fingerprint
                    if capabilities is not None
                    else ZERO_FINGERPRINT
                ),
                implementation_fingerprint=implementation_fingerprint,
                index_representation_fingerprint=representation_fingerprint,
                pr_overlay_representation_fingerprint=(
                    overlay_representation_fingerprint
                ),
                snapshots=snapshots,
            )
            reusable_receipt = (
                read_pr_overlay_generation(
                    index_manager.qdrant_client,
                    collection_name,
                    workspace=request.workspace,
                    project=request.project,
                    pr_number=request.pr_number,
                    branch=request.branch,
                    base_branch=target_branch,
                    source_revision=request.source_revision,
                    base_revision=request.base_revision,
                    base_generation_manifest_sha256=base_receipt[
                        "generation_manifest_sha256"
                    ],
                    generation_fingerprint=generation_fingerprint,
                    overlay_representation_fingerprint=(
                        overlay_representation_fingerprint
                    ),
                )
                if base_receipt else None
            )
            if reusable_receipt is not None or (
                base_receipt is None
                and is_complete_reusable_generation(
                    old_pr_points, generation_fingerprint
                )
            ):
                architecture_points = sum(
                    1
                    for point in old_pr_points
                    if (
                        (point.payload or {}).get("architecture_context")
                        or (point.payload or {}).get("architecture_source")
                    )
                )
                logger.info(
                    "Reused PR #%s overlay generation: %s points from %s changed files",
                    request.pr_number,
                    len(old_pr_points),
                    len(request.files),
                )
                return {
                    "status": "reused",
                    **base_generation_receipt,
                    "pr_number": request.pr_number,
                    "files_processed": len(request.files),
                    "chunks_indexed": len(old_pr_points),
                    "chunks_failed": 0,
                    "architecture_packets_indexed": architecture_points,
                    "generation_fingerprint": generation_fingerprint,
                    **(reusable_receipt or {}),
                    "overlay_representation_fingerprint": (
                        overlay_representation_fingerprint
                    ),
                    "partial_files": list(request_partial_files),
                    "effective_project_capabilities": _capabilities_payload(
                        capabilities,
                        implementation_fingerprint,
                    ),
                    "review_groups": review_groups_from_architecture_payloads(
                        (
                            point.payload or {}
                            for point in old_pr_points
                            if (point.payload or {}).get("architecture_context")
                        ),
                        tuple(file_info.path for file_info in request.files),
                    ),
                }

        file_dispositions = {}
        active_overlay_files = list(request.files)
        if capabilities is not None:
            from codecrow_plugins import FileDisposition

            file_dispositions = {
                file_info.path: index_manager.plugin_runtime.file_disposition(
                    file_info.path,
                    capabilities,
                )
                for file_info in request.files
            }
            active_overlay_files = [
                file_info
                for file_info in request.files
                if file_dispositions[file_info.path] not in {
                    FileDisposition.EXCLUDED,
                    FileDisposition.GENERATED,
                }
            ]

        partial_overlay_files = tuple(sorted(
            file_info.path
            for file_info in active_overlay_files
            if (
                file_info.change_type != "DELETED"
                and _content_state(file_info) != "complete"
            )
        ))
        if required_snapshot_plugins and partial_overlay_files:
            raise HTTPException(
                status_code=409,
                detail=(
                    "PR repository analysis requires complete changed-file "
                    "source; partial diff content was supplied for "
                    f"{', '.join(partial_overlay_files)}. Retrieve the complete "
                    "post-change source before review."
                ),
            )

        # Only complete post-change source is eligible for semantic embedding.
        # Partial diffs remain review evidence in the inference request and are
        # represented here only by their changed-file identity.
        documents = []
        for file_info in active_overlay_files:
            if not file_info.content or not file_info.content.strip():
                continue
            if file_info.change_type == "DELETED":
                continue
            if _content_state(file_info) != "complete":
                continue
            if capabilities is not None:
                disposition = file_dispositions[file_info.path]
                if disposition is not FileDisposition.FULL:
                    continue

            doc = LlamaDocument(
                text=file_info.content,
                metadata={
                    "path": file_info.path,
                    "change_type": file_info.change_type,
                    "content_state": "complete",
                }
            )
            documents.append(doc)

        if documents:
            chunks, split_skipped_paths = (
                index_manager.splitter.split_documents_resilient(
                    documents,
                    capabilities=capabilities,
                )
            )
        else:
            chunks = []
            split_skipped_paths = ()

        # Add PR metadata to all chunks
        for chunk in chunks:
            chunk.metadata["content_state"] = "complete"
            chunk.metadata[INDEX_REPRESENTATION_PAYLOAD_KEY] = (
                representation_fingerprint
            )
            chunk.metadata[PR_OVERLAY_REPRESENTATION_PAYLOAD_KEY] = (
                overlay_representation_fingerprint
            )
            if capabilities is not None:
                chunk.metadata["plugin_ids"] = list(
                    capabilities.repository_plugins
                )
                chunk.metadata["plugin_fingerprint"] = capabilities.fingerprint
                chunk.metadata["plugin_descriptor_fingerprint"] = (
                    capabilities.descriptor_fingerprint
                )
                chunk.metadata["plugin_implementation_fingerprint"] = (
                    implementation_fingerprint
                )
            chunk.metadata["pr"] = True
            chunk.metadata["pr_number"] = request.pr_number
            chunk.metadata["pr_branch"] = request.branch
            chunk.metadata["workspace"] = request.workspace
            chunk.metadata["project"] = request.project
            chunk.metadata["branch"] = request.branch
            if generation_fingerprint:
                chunk.metadata["pr_generation_fingerprint"] = (
                    generation_fingerprint
                )
                chunk.metadata["pr_source_revision"] = request.source_revision
                chunk.metadata["pr_base_revision"] = request.base_revision
                if base_receipt:
                    chunk.metadata["pr_base_generation_manifest_sha256"] = (
                        base_receipt["generation_manifest_sha256"]
                    )
                    chunk.metadata["pr_overlay_base_branch"] = target_branch
            chunk.metadata["indexed_at"] = datetime.now(timezone.utc).isoformat()

        point_id_branch = f"__pr__/{request.pr_number}/{request.branch}"
        analysis_revision = request.source_revision or f"pr-{request.pr_number}"

        architecture_nodes = []
        if capabilities is not None and (snapshots or fresh_repository_plugins):
            from codecrow_plugins import (
                FileArtifact,
                RepositoryAnalysis,
                RepositoryAnalysisMode,
            )

            handle = index_manager.plugin_runtime.start_repository_analysis(
                capabilities,
                analysis_revision,
                snapshots=snapshots,
                mode=RepositoryAnalysisMode.PR_OVERLAY,
            )
            artifacts = tuple(sorted(
                (
                    FileArtifact(
                        path=file_info.path,
                        content=file_info.content,
                        deleted=file_info.change_type == "DELETED",
                    )
                    for file_info in active_overlay_files
                ),
                key=lambda artifact: artifact.path,
            ))
            handle.ingest(artifacts)
            analysis, diagnostics = handle.finish()
            repository_skipped_paths = (
                index_manager._indexer
                .accept_recoverable_repository_diagnostics(
                    diagnostics,
                    "PR repository overlay",
                )
            )
            split_skipped_paths = tuple(sorted({
                *split_skipped_paths,
                *repository_skipped_paths,
            }))

            changed_paths = {
                file_info.path for file_info in active_overlay_files
            }
            affected_packets = tuple(
                packet for packet in analysis.packets
                if changed_paths.intersection(packet.paths)
            )
            affected_related_paths = {
                path for packet in affected_packets for path in packet.paths
            }
            affected_analysis = RepositoryAnalysis(
                packets=affected_packets,
                contexts=tuple(
                    context for context in analysis.contexts
                    if context.path in affected_related_paths
                ),
            )
            architecture_nodes = index_manager._indexer._architecture_nodes(
                affected_analysis,
                capabilities,
                request.workspace,
                request.project,
                request.branch,
                analysis_revision,
                implementation_fingerprint,
                representation_fingerprint,
            )
            architecture_nodes.extend(
                index_manager._indexer._repository_context_nodes(
                    affected_analysis,
                    capabilities,
                    request.workspace,
                    request.project,
                    request.branch,
                    analysis_revision,
                    implementation_fingerprint,
                    representation_fingerprint,
                )
            )
            for node in architecture_nodes:
                node.metadata["pr"] = True
                node.metadata["pr_number"] = request.pr_number
                node.metadata["pr_branch"] = request.branch
                node.metadata[PR_OVERLAY_REPRESENTATION_PAYLOAD_KEY] = (
                    overlay_representation_fingerprint
                )
                if generation_fingerprint:
                    node.metadata["pr_generation_fingerprint"] = (
                        generation_fingerprint
                    )
                    node.metadata["pr_source_revision"] = request.source_revision
                    node.metadata["pr_base_revision"] = request.base_revision
                    if base_receipt:
                        node.metadata["pr_base_generation_manifest_sha256"] = (
                            base_receipt["generation_manifest_sha256"]
                        )
                        node.metadata["pr_overlay_base_branch"] = target_branch
                node.metadata["indexed_at"] = datetime.now(timezone.utc).isoformat()
        overlay_receipt = {}
        if generation_fingerprint and base_receipt:
            identity_metadata = {
                "plugin_ids": list(
                    capabilities.repository_plugins
                    if capabilities is not None else stored_plugin_ids
                ),
                "plugin_fingerprint": (
                    capabilities.fingerprint
                    if capabilities is not None else stored_fingerprint
                ) or ZERO_FINGERPRINT,
                "plugin_descriptor_fingerprint": (
                    capabilities.descriptor_fingerprint
                    if capabilities is not None
                    else _stored_descriptor_fingerprint
                ) or ZERO_FINGERPRINT,
                "plugin_implementation_fingerprint": (
                    implementation_fingerprint or ZERO_FINGERPRINT
                ),
                INDEX_REPRESENTATION_PAYLOAD_KEY: representation_fingerprint,
                PR_OVERLAY_REPRESENTATION_PAYLOAD_KEY: (
                    overlay_representation_fingerprint
                ),
            }

            successful, overlay_receipt = (
                index_manager._file_ops.replace_pr_overlay_generation(
                    [*chunks, *architecture_nodes],
                    old_pr_points,
                    collection_name,
                    request.workspace,
                    request.project,
                    point_id_branch,
                    mutation_lease.assert_owned,
                    pr_number=request.pr_number,
                    branch=request.branch,
                    base_branch=target_branch,
                    source_revision=request.source_revision,
                    base_revision=request.base_revision,
                    base_generation_manifest_sha256=base_receipt[
                        "generation_manifest_sha256"
                    ],
                    generation_fingerprint=generation_fingerprint,
                    overlay_representation_fingerprint=(
                        overlay_representation_fingerprint
                    ),
                    identity_metadata=identity_metadata,
                )
            )
        else:
            successful = index_manager._file_ops._replace_points(
                [*chunks, *architecture_nodes],
                old_pr_points,
                collection_name,
                request.workspace,
                request.project,
                point_id_branch,
                mutation_lease.assert_owned,
            )
        skipped_points = (
            len(chunks) + len(architecture_nodes) - successful
        )

        logger.info(
            "Indexed PR #%s: %s semantic/architecture points from %s changed files (%s architecture packets)",
            request.pr_number,
            successful,
            len(request.files),
            len(architecture_nodes),
        )

        return {
            "status": "indexed",
            **base_generation_receipt,
            "pr_number": request.pr_number,
            "files_processed": len(request.files),
            "chunks_indexed": successful,
            "chunks_failed": 0,
            "chunks_skipped": skipped_points,
            "skipped_files": list(split_skipped_paths),
            "architecture_packets_indexed": len(architecture_nodes),
            "generation_fingerprint": generation_fingerprint,
            **overlay_receipt,
            "overlay_representation_fingerprint": (
                overlay_representation_fingerprint
            ),
            "partial_files": list(request_partial_files),
            "effective_project_capabilities": _capabilities_payload(
                capabilities,
                implementation_fingerprint,
            ),
            "review_groups": review_groups_from_architecture_payloads(
                (
                    node.metadata
                    for node in architecture_nodes
                    if node.metadata.get("architecture_context")
                ),
                tuple(file_info.path for file_info in active_overlay_files),
            ),
        }

    except HTTPException:
        raise
    except IncrementalIndexPreconditionError as e:
        logger.info("Rejected PR indexing against invalid repository state: %s", e)
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        logger.info(f"Invalid request for PR indexing: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Internal error indexing PR files: {e}")
        raise HTTPException(status_code=500, detail="Internal indexing error")
    finally:
        if mutation_lease is not None:
            mutation_context.__exit__(None, None, None)


@router.delete("/index/pr-files/{workspace}/{project}/{pr_number}")
def delete_pr_files(
    workspace: str,
    project: str,
    pr_number: int,
    collection_target: str | None = Query(default=None),
):
    """Delete all indexed points for a specific PR."""
    index_manager = _get_index_manager()
    if not isinstance(collection_target, str) or not collection_target.strip():
        collection_target = None
    try:
        with index_manager.pr_overlay_mutation(
            workspace,
            project,
            pr_number,
            "delete-pr-overlay",
        ) as lease:
            collection_name = (
                collection_target
                or index_manager._get_project_collection_name(workspace, project)
            )
            physical_collection = (
                index_manager._collection_manager.resolve_collection_target(
                    collection_name
                )
            )
            if physical_collection is None:
                return {"status": "skipped", "message": "Collection does not exist"}

            # Existing exact generations may predate the PR filter indexes.
            # Repair them before the acknowledged filter delete. Keeping
            # wait=True is correctness-critical: releasing the same-PR lease
            # before Qdrant applies the delete could erase a subsequent rerun.
            index_manager._collection_manager.ensure_payload_indexes(
                physical_collection
            )

            lease.assert_owned()
            index_manager.qdrant_client.delete(
                collection_name=physical_collection,
                points_selector=Filter(
                    must=[
                        FieldCondition(key="workspace", match=MatchValue(value=workspace)),
                        FieldCondition(key="project", match=MatchValue(value=project)),
                        FieldCondition(key="pr", match=MatchValue(value=True)),
                        FieldCondition(key="pr_number", match=MatchValue(value=pr_number)),
                    ]
                ),
                wait=True,
            )

            logger.info(
                "Deleted PR #%s points from %s",
                pr_number,
                physical_collection,
            )

            return {
                "status": "deleted",
                "pr_number": pr_number,
                "collection": physical_collection
            }

    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.debug("PR file deletion failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
