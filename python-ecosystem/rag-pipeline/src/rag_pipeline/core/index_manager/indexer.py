"""
Repository indexing operations.

Handles full repository indexing with atomic swap and streaming processing.
"""

import gc
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, List

from llama_index.core.schema import TextNode
from qdrant_client.models import (
    Filter,
    FieldCondition,
    MatchAny,
    MatchValue,
    PointIdsList,
    PointStruct,
)

from ...models.config import RAGConfig, IndexStats
from ...utils.utils import clean_archive_path, make_namespace
from ..index_representation import (
    INDEX_REPRESENTATION_PAYLOAD_KEY,
    index_representation_fingerprint,
    observe_branch_representation,
)
from ..generation_manifest import (
    build_generation_manifest_node,
    collect_generation_members,
    compute_generation_members_digest,
    seal_generation_members,
)
from ..source_tree import require_repository_source_tree_unchanged
from .collection_manager import CollectionManager
from .branch_manager import BranchManager
from .point_operations import PointOperations
from .stats_manager import StatsManager

logger = logging.getLogger(__name__)

# Memory-efficient batch sizes
DOCUMENT_BATCH_SIZE = 50
INSERT_BATCH_SIZE = 128


def _plugin_identity_metadata(
    capabilities,
    implementation_fingerprint: str,
    representation_fingerprint: Optional[str] = None,
):
    metadata = {
        "plugin_ids": [],
        "plugin_fingerprint": "sha256:" + "0" * 64,
        "plugin_descriptor_fingerprint": "sha256:" + "0" * 64,
        "plugin_implementation_fingerprint": "sha256:" + "0" * 64,
        INDEX_REPRESENTATION_PAYLOAD_KEY: (
            representation_fingerprint
            or index_representation_fingerprint()
        ),
    }
    if capabilities is None:
        return metadata
    metadata.update({
        "plugin_ids": list(capabilities.repository_plugins),
        "plugin_fingerprint": capabilities.fingerprint,
        "plugin_descriptor_fingerprint": capabilities.descriptor_fingerprint,
        "plugin_implementation_fingerprint": implementation_fingerprint,
    })
    return metadata


class RepositoryIndexer:
    """Handles repository indexing operations."""
    
    def __init__(
        self,
        config: RAGConfig,
        collection_manager: CollectionManager,
        branch_manager: BranchManager,
        point_ops: PointOperations,
        stats_manager: StatsManager,
        splitter,
        loader,
        plugin_catalog=None,
        plugin_runtime=None,
        plugin_selector=None,
    ):
        self.config = config
        self.collection_manager = collection_manager
        self.branch_manager = branch_manager
        self.point_ops = point_ops
        self.stats_manager = stats_manager
        self.splitter = splitter
        self.loader = loader
        self.plugin_catalog = plugin_catalog
        self.plugin_runtime = plugin_runtime
        self.plugin_selector = plugin_selector
        self.representation_fingerprint = index_representation_fingerprint(
            config
        )

    @staticmethod
    def accept_recoverable_repository_diagnostics(
        diagnostics,
        phase: str,
    ) -> set[str]:
        """Log quarantined project inputs and reject only runtime-level faults."""
        recoverable = [
            diagnostic for diagnostic in diagnostics
            if diagnostic.recoverable
        ]
        fatal = [
            diagnostic for diagnostic in diagnostics
            if not diagnostic.recoverable
        ]
        for diagnostic in recoverable:
            logger.warning(
                "Skipping invalid repository input during %s "
                "(plugin=%s code=%s path=%s): %s",
                phase,
                diagnostic.plugin_id or "plugin",
                diagnostic.code,
                diagnostic.path or "<repository>",
                diagnostic.message,
            )
        if fatal:
            summary = "; ".join(
                f"{diagnostic.plugin_id or 'plugin'}:{diagnostic.code}: "
                f"{diagnostic.message}"
                for diagnostic in fatal[:10]
            )
            raise RuntimeError(f"{phase} failed: {summary}")
        return {
            diagnostic.path
            for diagnostic in recoverable
            if diagnostic.path is not None
        }

    @staticmethod
    def _architecture_nodes(
        analysis,
        capabilities,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        implementation_fingerprint: str = "sha256:" + "0" * 64,
        representation_fingerprint: Optional[str] = None,
        groups: Optional[set[tuple[str, str, str]]] = None,
    ) -> List[TextNode]:
        """Serialize neutral architecture packets into bounded retrieval nodes.

        ``groups`` is the neutral incremental invalidation boundary.  It is
        deliberately expressed only in contract data (plugin, fact kind and
        source path), so the host never needs framework-specific knowledge.
        """
        nodes: List[TextNode] = []
        facts_per_node = 25
        grouped = {}
        from ..repository_overlay import architecture_group_id

        for packet in analysis.packets:
            for fact in packet.facts:
                identity = (packet.plugin_id, packet.kind, fact.path)
                if groups is not None and identity not in groups:
                    continue
                grouped.setdefault(identity, []).append((packet, fact))

        for (plugin_id, packet_kind, source_path), records in sorted(grouped.items()):
            group_id = architecture_group_id((plugin_id, packet_kind, source_path))
            records = sorted(records, key=lambda item: (item[0].key, item[1]))
            for offset in range(0, len(records), facts_per_node):
                segment = records[offset:offset + facts_per_node]
                identity = f"{plugin_id}\0{packet_kind}\0{source_path}\0{offset // facts_per_node}"
                digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                synthetic_path = f"__analysis_architecture__/{plugin_id}/{digest}.context"
                related_paths = sorted({
                    path
                    for _, fact in segment
                    for path in (fact.path, *fact.related_paths)
                })
                fact_payload = [
                    {
                        **dict(fact.as_metadata()),
                        "packetKey": packet.key,
                        "packetAttributes": dict(packet.attributes),
                    }
                    for packet, fact in segment
                ]
                identifiers = sorted({
                    value
                    for _, fact in segment
                    for value in (fact.source, fact.target)
                    if value
                })
                packet_keys = sorted({packet.key for packet, _ in segment})
                header = [
                    "Deterministic repository architecture context",
                    f"Plugin: {plugin_id}",
                    f"Kind: {packet_kind}",
                    f"Source: {source_path}",
                    "Packet keys: " + ", ".join(packet_keys),
                ]
                header.append("Related paths: " + ", ".join(related_paths))
                fact_lines = [
                    (
                        f"- Packet {packet.key}: [{fact.kind}] "
                        f"{fact.source} {fact.relation} {fact.target} "
                        f"({fact.path}:{fact.line})"
                        + (
                            " {" + ", ".join(
                                f"{key}={value}" for key, value in fact.attributes
                            ) + "}"
                            if fact.attributes else ""
                        )
                    )
                    for packet, fact in segment
                ]
                nodes.append(TextNode(
                    text="\n".join((*header, "Facts:", *fact_lines)),
                    metadata={
                        "workspace": workspace,
                        "project": project,
                        "branch": branch,
                        "commit": commit,
                        "path": synthetic_path,
                        "language": "architecture-context",
                        "filetype": "context",
                        "architecture_context": True,
                        "architecture_plugin": plugin_id,
                        "architecture_kind": packet_kind,
                        "architecture_source_path": source_path,
                        "architecture_group": group_id,
                        "architecture_key": f"{packet_kind}:{source_path}:{offset // facts_per_node}",
                        "architecture_keys": packet_keys,
                        "architecture_paths": related_paths,
                        "architecture_identifiers": identifiers,
                        **_plugin_identity_metadata(
                            capabilities,
                            implementation_fingerprint,
                            representation_fingerprint,
                        ),
                        "plugin_graph_facts": fact_payload,
                    },
                ))
        return nodes

    @staticmethod
    def _snapshot_nodes(
        analysis,
        capabilities,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        implementation_fingerprint: str = "sha256:" + "0" * 64,
        representation_fingerprint: Optional[str] = None,
    ) -> List[TextNode]:
        """Store opaque plugin snapshots in bounded zero-vector payload nodes."""
        nodes: List[TextNode] = []
        part_size = 400_000
        for snapshot in analysis.snapshots:
            identity = f"{snapshot.plugin_id}\0{snapshot.kind}"
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            content_digest = hashlib.sha256(snapshot.content.encode("utf-8")).hexdigest()
            parts = [
                snapshot.content[offset:offset + part_size]
                for offset in range(0, len(snapshot.content), part_size)
            ]
            for part_index, content in enumerate(parts):
                nodes.append(TextNode(
                    text=content,
                    metadata={
                        "workspace": workspace,
                        "project": project,
                        "branch": branch,
                        "commit": commit,
                        "path": (
                            f"__analysis_state__/{snapshot.plugin_id}/{digest}/"
                            f"{part_index:06d}.state"
                        ),
                        "language": "repository-state",
                        "filetype": "state",
                        "repository_snapshot": True,
                        "snapshot_plugin": snapshot.plugin_id,
                        "snapshot_kind": snapshot.kind,
                        "snapshot_part": part_index,
                        "snapshot_parts": len(parts),
                        "snapshot_content_sha256": content_digest,
                        **_plugin_identity_metadata(
                            capabilities,
                            implementation_fingerprint,
                            representation_fingerprint,
                        ),
                    },
                ))
        return nodes

    @staticmethod
    def _repository_facts_nodes(
        repository_facts,
        capabilities,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        implementation_fingerprint: str = "sha256:" + "0" * 64,
        representation_fingerprint: Optional[str] = None,
    ) -> List[TextNode]:
        """Persist the complete neutral inventory used for plugin selection."""
        content = json.dumps(
            {
                "revision": repository_facts.revision,
                "paths": list(repository_facts.paths),
                "markerContents": dict(repository_facts.marker_contents),
                "projectType": repository_facts.project_type,
                "sourceRoot": repository_facts.source_root,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        part_size = 400_000
        parts = [
            content[offset:offset + part_size]
            for offset in range(0, len(content), part_size)
        ]
        return [
            TextNode(
                text=part,
                metadata={
                    "workspace": workspace,
                    "project": project,
                    "branch": branch,
                    "commit": commit,
                    "path": (
                        "__analysis_state__/repository-facts/"
                        f"{part_index:06d}.state"
                    ),
                    "language": "repository-state",
                    "filetype": "state",
                    "repository_facts_state": True,
                    "facts_part": part_index,
                    "facts_parts": len(parts),
                    "facts_content_sha256": content_digest,
                    **_plugin_identity_metadata(
                        capabilities,
                        implementation_fingerprint,
                        representation_fingerprint,
                    ),
                },
            )
            for part_index, part in enumerate(parts)
        ]

    @staticmethod
    def _repository_context_nodes(
        analysis,
        capabilities,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        implementation_fingerprint: str = "sha256:" + "0" * 64,
        representation_fingerprint: Optional[str] = None,
    ) -> List[TextNode]:
        """Serialize exact plugin-selected sources without embedding them."""
        nodes: List[TextNode] = []
        part_size = 50_000
        for context in analysis.contexts:
            parts = [
                context.content[offset:offset + part_size]
                for offset in range(0, len(context.content), part_size)
            ]
            for part_index, content in enumerate(parts):
                nodes.append(TextNode(
                    text=content,
                    metadata={
                        "workspace": workspace,
                        "project": project,
                        "branch": branch,
                        "commit": commit,
                        "path": context.path,
                        "language": "architecture-source",
                        "filetype": context.path.rsplit(".", 1)[-1],
                        "architecture_source": True,
                        "architecture_plugin": context.plugin_id,
                        "architecture_source_kind": context.kind,
                        "architecture_source_part": part_index,
                        "architecture_source_parts": len(parts),
                        **_plugin_identity_metadata(
                            capabilities,
                            implementation_fingerprint,
                            representation_fingerprint,
                        ),
                        **dict(context.attributes),
                    },
                ))
        return nodes
    
    def estimate_repository_size(
        self,
        repo_path: str,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None
    ) -> tuple[int, int]:
        """Estimate repository size (file count and chunk count) without actually indexing."""
        logger.info(f"Estimating repository size for: {repo_path}")

        repo_path_obj = Path(repo_path)
        file_list = list(self.loader.iter_repository_files(repo_path_obj, include_patterns, exclude_patterns))
        if self.plugin_catalog is not None and self.plugin_selector is not None:
            from codecrow_plugins import FileDisposition, build_repository_facts

            estimate_capabilities = self.plugin_selector.select(build_repository_facts(
                repo_path_obj,
                "estimate",
                file_list,
                self.plugin_catalog.registry,
            ))
            if self.plugin_runtime is not None:
                file_list = [
                    path
                    for path in file_list
                    if self.plugin_runtime.file_disposition(
                        clean_archive_path(Path(path).as_posix()), estimate_capabilities
                    ) is FileDisposition.FULL
                ]
        file_count = len(file_list)
        logger.info(
            "RAG capacity scan found %s repository files "
            "(this is not the LLM review scope)",
            file_count,
        )

        if file_count == 0:
            return 0, 0

        SAMPLE_SIZE = 100
        chunk_count = 0
        
        if file_count <= SAMPLE_SIZE:
            for i in range(0, file_count, DOCUMENT_BATCH_SIZE):
                batch = file_list[i:i + DOCUMENT_BATCH_SIZE]
                documents = self.loader.load_file_batch(
                    batch, repo_path_obj, "estimate", "estimate", "estimate", "estimate"
                )
                if documents:
                    chunks = self.splitter.split_documents(documents)
                    chunk_count += len(chunks)
                    del chunks
                del documents
                gc.collect()
        else:
            # Stable spread across the normalized list. Sampling must not change
            # index admission decisions between identical runs.
            ordered_files = sorted(file_list, key=lambda path: str(path).replace("\\", "/"))
            sample_files = [
                ordered_files[(index * len(ordered_files)) // SAMPLE_SIZE]
                for index in range(SAMPLE_SIZE)
            ]
            sample_chunk_count = 0
            
            for i in range(0, len(sample_files), DOCUMENT_BATCH_SIZE):
                batch = sample_files[i:i + DOCUMENT_BATCH_SIZE]
                documents = self.loader.load_file_batch(
                    batch, repo_path_obj, "estimate", "estimate", "estimate", "estimate"
                )
                if documents:
                    chunks = self.splitter.split_documents(documents)
                    sample_chunk_count += len(chunks)
                    del chunks
                del documents
                gc.collect()
            
            avg_chunks_per_file = sample_chunk_count / SAMPLE_SIZE
            chunk_count = int(avg_chunks_per_file * file_count)
            logger.info(f"Estimated ~{avg_chunks_per_file:.1f} chunks/file from {SAMPLE_SIZE} samples")
            gc.collect()

        logger.info(f"Estimated {chunk_count} chunks from {file_count} files")
        return file_count, chunk_count
    
    def index_repository(
        self,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        alias_name: str,
        preserve_other_branches: bool = False,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        source_tree_sha256: Optional[str] = None,
        source_tree=None,
        seal_generation: bool = False,
        publication_aliases: Optional[List[str]] = None,
        reuse_collection_name: Optional[str] = None,
        operation_id: Optional[str] = None,
        activation_guard: Optional[Callable[[], None]] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
    ) -> IndexStats:
        """Index entire repository for a branch using atomic swap strategy."""
        def report_progress(
            stage: str,
            message: str,
            progress: Optional[int] = None,
            total: Optional[int] = None,
            **details,
        ) -> None:
            if progress_callback is None:
                return
            event = {"stage": stage, "message": message}
            if progress is not None:
                event["progress"] = max(0, min(100, progress))
            if total is not None:
                event["total"] = total
            event.update({key: value for key, value in details.items()
                          if value is not None})
            try:
                progress_callback(event)
            except Exception as exception:
                # Progress reporting is optional enrichment. It must never fail
                # an otherwise healthy index operation.
                logger.warning(
                    "RAG progress callback failed at stage %s: %s",
                    stage,
                    exception,
                )

        operation_id = operation_id or hashlib.sha256(
            f"{workspace}\0{project}\0{branch}\0{commit}\0{time.time_ns()}".encode()
        ).hexdigest()[:32]
        operation_started = time.perf_counter()
        logger.info(
            "Indexing repository operation_id=%s workspace=%s project=%s "
            "branch=%s repo_path=%s",
            operation_id,
            workspace,
            project,
            branch,
            repo_path,
        )
        report_progress("preparing", "Preparing the pending vector collection", 2)

        repo_path_obj = Path(repo_path)
        pending_collection_name = self.collection_manager.create_pending_collection(
            alias_name,
            operation_id=operation_id,
        )

        activation_aliases = list(dict.fromkeys(
            [alias_name, *(publication_aliases or [])]
        ))
        activation_alias_targets = self.collection_manager.read_alias_targets(
            activation_aliases
        )

        # Check existing collection and preserve other branch data using streaming
        old_alias_exists = self.collection_manager.alias_exists(alias_name)
        old_collection_exists = old_alias_exists or self.collection_manager.collection_exists(alias_name)

        if old_collection_exists and not old_alias_exists:
            self.collection_manager.delete_collection(pending_collection_name)
            raise RuntimeError(
                "Atomic index activation requires an alias-backed collection; "
                "the existing direct collection was left unchanged"
            )
        
        actual_old_collection = None
        if old_collection_exists:
            actual_old_collection = self.collection_manager.resolve_alias(alias_name) or alias_name
        vector_reuse_collection = reuse_collection_name or actual_old_collection
        
        # Get file list
        repository_file_list = list(
            self.loader.iter_repository_files(
                repo_path_obj,
                include_patterns,
                exclude_patterns,
                expected_file_sha256=(
                    source_tree.file_sha256_by_path if source_tree else None
                ),
            )
        )
        logger.info(
            "Found %s repository files before plugin file policy for branch '%s'",
            len(repository_file_list),
            branch,
        )
        report_progress(
            "scanning",
            f"Discovered {len(repository_file_list)} repository files",
            7,
            len(repository_file_list),
        )
        
        if not repository_file_list:
            logger.warning("No documents to index")
            self.collection_manager.delete_collection(pending_collection_name)
            return self.stats_manager.get_branch_stats(
                workspace, project, branch,
                self.collection_manager.resolve_alias(alias_name) or alias_name
            )

        capabilities = None
        implementation_fingerprint = "sha256:" + "0" * 64
        repository_analysis = None
        repository_facts = None
        if self.plugin_catalog is not None and self.plugin_selector is not None:
            from codecrow_plugins import build_repository_facts

            repository_facts = build_repository_facts(
                repo_path_obj,
                commit,
                repository_file_list,
                self.plugin_catalog.registry,
                project_type=project_type,
                source_root=source_root,
            )
            capabilities = self.plugin_selector.select(repository_facts)
            implementation_fingerprint = (
                self.plugin_catalog.implementation_fingerprint(
                    capabilities.repository_plugins
                )
            )
            logger.info(
                "Selected plugins for %s: %s",
                commit,
                ", ".join(capabilities.repository_plugins) or "generic fallback",
            )
            report_progress(
                "framework",
                "Selected repository plugins: "
                + (", ".join(capabilities.repository_plugins) or "generic fallback"),
                10,
            )

        file_list = repository_file_list
        semantic_paths = {
            clean_archive_path(Path(path).as_posix()) for path in file_list
        }
        if self.plugin_runtime is not None and capabilities is not None:
            from codecrow_plugins import FileDisposition

            dispositions = {
                clean_archive_path(Path(path).as_posix()): self.plugin_runtime.file_disposition(
                    clean_archive_path(Path(path).as_posix()), capabilities
                )
                for path in repository_file_list
            }
            file_list = [
                path for path in repository_file_list
                if dispositions[clean_archive_path(Path(path).as_posix())]
                not in {
                    FileDisposition.EXCLUDED,
                    FileDisposition.GENERATED,
                }
            ]
            semantic_paths = {
                path
                for path, disposition in dispositions.items()
                if disposition is FileDisposition.FULL
            }
            logger.info(
                "Plugin file policy selected %s semantic files and %s architecture-only files",
                len(semantic_paths),
                len(file_list) - len(semantic_paths),
            )
        total_files = len(semantic_paths)
        report_progress(
            "scope",
            f"Selected {total_files} semantic files for indexing",
            12,
            total_files,
        )

        analysis_handle = None
        if self.plugin_runtime is not None and capabilities is not None:
            analysis_handle = self.plugin_runtime.start_repository_analysis(
                capabilities,
                commit,
                source_root=repository_facts.source_root if repository_facts else None,
            )
        
        # Validate limits
        if self.config.max_files_per_index > 0 and total_files > self.config.max_files_per_index:
            self.collection_manager.delete_collection(pending_collection_name)
            raise ValueError(
                f"Repository exceeds file limit: {total_files} files (max: {self.config.max_files_per_index})."
            )
        
        if self.config.max_chunks_per_index > 0:
            logger.info("Estimating chunk count before indexing...")
            report_progress("estimating", "Estimating repository chunk count", 14)
            _, estimated_chunks = self.estimate_repository_size(
                repo_path,
                include_patterns,
                exclude_patterns
            )
            if estimated_chunks > self.config.max_chunks_per_index * 1.2:
                self.collection_manager.delete_collection(pending_collection_name)
                raise ValueError(
                    f"Repository estimated to exceed chunk limit: ~{estimated_chunks} chunks (max: {self.config.max_chunks_per_index})."
                )

        document_count = 0
        chunk_count = 0
        successful_chunks = 0
        skipped_chunk_count = 0
        skipped_file_paths: set[str] = set()
        preserved_point_count = 0
        embedding_metrics = {"reused": 0, "embedded": 0}
        estimated_chunks = None

        # Chunk totals are an estimate rather than an expensive second exact
        # indexing pass.  Progress remains useful even when estimation fails.
        if progress_callback is not None:
            try:
                _, estimated_chunks = self.estimate_repository_size(
                    repo_path, include_patterns, exclude_patterns
                )
                report_progress(
                    "estimating",
                    f"Estimated approximately {estimated_chunks} chunks",
                    14,
                    estimatedChunks=estimated_chunks,
                )
            except Exception as exception:
                logger.warning("Could not estimate RAG progress chunks: %s", exception)

        try:
            # A main-only project must not carry stale non-target branches into
            # its next authoritative generation. Multi-branch projects opt in
            # explicitly through the host-owned project configuration.
            if actual_old_collection and preserve_other_branches:
                copy_batch_size = getattr(self.point_ops, "batch_size", None)
                if not isinstance(copy_batch_size, int) or copy_batch_size <= 0:
                    copy_batch_size = INSERT_BATCH_SIZE
                preserved_point_count = (
                    self.branch_manager.stream_copy_points_to_collection(
                        actual_old_collection,
                        pending_collection_name,
                        branch,
                        copy_batch_size,
                    )
                )
            
            # Stream process files in batches
            logger.info("Starting memory-efficient streaming indexing...")
            batch_num = 0
            total_batches = (len(file_list) + DOCUMENT_BATCH_SIZE - 1) // DOCUMENT_BATCH_SIZE
            report_progress(
                "indexing",
                f"Starting {total_batches} indexing batches",
                18,
                total_batches,
                totalBatches=total_batches,
                indexedChunks=0,
                estimatedChunks=estimated_chunks,
            )
            
            # Architecture-only files still have to reach the repository
            # resolver.  ``total_files`` counts only embedding-bearing files
            # for admission control, so using it as the iteration bound would
            # silently truncate the resolver input whenever a plugin removes
            # files from semantic indexing.
            for i in range(0, len(file_list), DOCUMENT_BATCH_SIZE):
                batch_num += 1
                file_batch = file_list[i:i + DOCUMENT_BATCH_SIZE]
                batch_started = time.perf_counter()
                load_started = time.perf_counter()
                documents = self.loader.load_file_batch(
                    file_batch, repo_path_obj, workspace, project, branch, commit,
                    strict=False,
                    expected_file_sha256=(
                        source_tree.file_sha256_by_path if source_tree else None
                    ),
                )
                load_duration_ms = round(
                    (time.perf_counter() - load_started) * 1000
                )
                loaded_paths = {
                    document.metadata["path"] for document in documents
                }
                missing_paths = {
                    clean_archive_path(Path(path).as_posix())
                    for path in file_batch
                } - loaded_paths
                for path in sorted(missing_paths):
                    logger.warning(
                        "Skipping repository file that could not be loaded: %s",
                        path,
                    )
                skipped_file_paths.update(missing_paths)
                if not documents:
                    continue

                if analysis_handle is not None and analysis_handle.active:
                    from codecrow_plugins import FileArtifact

                    artifacts = tuple(sorted(
                        (
                            FileArtifact(
                                path=document.metadata["path"],
                                content=document.text,
                            )
                            for document in documents
                        ),
                        key=lambda artifact: artifact.path,
                    ))
                    analysis_handle.ingest(artifacts)

                semantic_documents = [
                    document
                    for document in documents
                    if document.metadata["path"] in semantic_paths
                ]
                if not semantic_documents:
                    del documents
                    continue
                
                split_started = time.perf_counter()
                chunks, split_skipped_paths = (
                    self.splitter.split_documents_resilient(
                    semantic_documents,
                    capabilities=capabilities,
                    )
                )
                split_duration_ms = round(
                    (time.perf_counter() - split_started) * 1000
                )
                skipped_file_paths.update(split_skipped_paths)
                document_count += (
                    len(semantic_documents) - len(split_skipped_paths)
                )
                identity_metadata = _plugin_identity_metadata(
                    capabilities,
                    implementation_fingerprint,
                    self.representation_fingerprint,
                )
                for chunk in chunks:
                    chunk.metadata.update(identity_metadata)
                batch_chunk_count = len(chunks)
                chunk_count += batch_chunk_count
                
                # Check chunk limit
                if self.config.max_chunks_per_index > 0 and chunk_count > self.config.max_chunks_per_index:
                    self.collection_manager.delete_collection(pending_collection_name)
                    raise ValueError(f"Repository exceeds chunk limit: {chunk_count}+ chunks.")
                
                # Process and upsert
                point_pipeline_started = time.perf_counter()
                success, failed = self.point_ops.process_and_upsert_chunks(
                    chunks,
                    pending_collection_name,
                    workspace,
                    project,
                    branch,
                    reuse_collection_name=vector_reuse_collection,
                    operation_id=operation_id,
                    metrics=embedding_metrics,
                )
                point_pipeline_duration_ms = round(
                    (time.perf_counter() - point_pipeline_started) * 1000
                )
                successful_chunks += success
                skipped_chunk_count += failed
                if failed:
                    logger.warning(
                        "Skipped %s rejected chunks in batch %s; "
                        "continuing repository indexing",
                        failed,
                        batch_num,
                    )
                
                logger.info(
                    "RAG document batch completed operation_id=%s batch=%s/%s "
                    "semantic_files=%s chunks=%s load_duration_ms=%s "
                    "split_duration_ms=%s point_pipeline_duration_ms=%s "
                    "duration_ms=%s",
                    operation_id,
                    batch_num,
                    total_batches,
                    len(semantic_documents),
                    batch_chunk_count,
                    load_duration_ms,
                    split_duration_ms,
                    point_pipeline_duration_ms,
                    round((time.perf_counter() - batch_started) * 1000),
                )
                batch_progress = 18 + round(67 * batch_num / max(total_batches, 1))
                elapsed_ms = round((time.perf_counter() - operation_started) * 1000)
                average_batch_ms = elapsed_ms / batch_num
                estimated_remaining_ms = round(
                    average_batch_ms * max(total_batches - batch_num, 0)
                )
                report_progress(
                    "indexing",
                    (
                        f"Indexed batch {batch_num}/{total_batches}: "
                        f"{document_count}/{total_files} files, "
                        f"{successful_chunks} chunks"
                    ),
                    batch_progress,
                    total_batches,
                    indexedChunks=successful_chunks,
                    estimatedChunks=estimated_chunks,
                    completedBatches=batch_num,
                    totalBatches=total_batches,
                    batchDurationMs=round(
                        (time.perf_counter() - batch_started) * 1000
                    ),
                    estimatedRemainingMs=estimated_remaining_ms,
                )
                
                del documents
                del chunks
                
                if batch_num % 5 == 0:
                    gc.collect()

            analysis_nodes = []
            architecture_nodes = []
            context_nodes = []
            snapshot_nodes = []
            if analysis_handle is not None:
                report_progress(
                    "architecture",
                    "Building deterministic architecture context",
                    88,
                )
                repository_analysis, diagnostics = analysis_handle.finish()
                skipped_file_paths.update(
                    self.accept_recoverable_repository_diagnostics(
                        diagnostics,
                        "repository architecture analysis",
                    )
                )

                architecture_nodes = self._architecture_nodes(
                    repository_analysis,
                    capabilities,
                    workspace,
                    project,
                    branch,
                    commit,
                    implementation_fingerprint,
                    self.representation_fingerprint,
                )
                snapshot_nodes = self._snapshot_nodes(
                    repository_analysis,
                    capabilities,
                    workspace,
                    project,
                    branch,
                    commit,
                    implementation_fingerprint,
                    self.representation_fingerprint,
                )
                context_nodes = self._repository_context_nodes(
                    repository_analysis,
                    capabilities,
                    workspace,
                    project,
                    branch,
                    commit,
                    implementation_fingerprint,
                    self.representation_fingerprint,
                )
                analysis_nodes.extend(
                    (*architecture_nodes, *context_nodes, *snapshot_nodes)
                )

            facts_nodes = []
            if repository_facts is not None:
                facts_nodes = self._repository_facts_nodes(
                    repository_facts,
                    capabilities,
                    workspace,
                    project,
                    branch,
                    commit,
                    implementation_fingerprint,
                    self.representation_fingerprint,
                )
                analysis_nodes.extend(facts_nodes)

            if analysis_nodes:
                architecture_count = len(analysis_nodes)
                chunk_count += architecture_count
                if (
                    self.config.max_chunks_per_index > 0
                    and chunk_count > self.config.max_chunks_per_index
                ):
                    raise ValueError(
                        f"Repository exceeds chunk limit after architecture analysis: {chunk_count} chunks."
                    )
                success, failed = self.point_ops.process_and_upsert_chunks(
                    analysis_nodes,
                    pending_collection_name,
                    workspace,
                    project,
                    branch,
                    reuse_collection_name=vector_reuse_collection,
                    operation_id=operation_id,
                    metrics=embedding_metrics,
                )
                successful_chunks += success
                skipped_chunk_count += failed
                if failed:
                    logger.warning(
                        "Skipped %s rejected deterministic context points; "
                        "continuing repository indexing",
                        failed,
                    )
                logger.info(
                    "Indexed %s deterministic architecture packets, %s exact source parts, "
                    "%s repository snapshot parts, and %s repository-fact state parts",
                    len(architecture_nodes),
                    len(context_nodes),
                    len(snapshot_nodes),
                    len(facts_nodes),
                )

            generation_manifest_sha256 = None
            generation_manifest_points = 0
            if seal_generation:
                report_progress(
                    "sealing",
                    "Sealing persisted vectors for generation integrity",
                    91,
                    indexedChunks=successful_chunks,
                    estimatedChunks=estimated_chunks,
                )
                seal_generation_members(
                    self.point_ops.client,
                    pending_collection_name,
                    branch,
                    commit,
                )
                identity_metadata = _plugin_identity_metadata(
                    capabilities,
                    implementation_fingerprint,
                    self.representation_fingerprint,
                )
                members = collect_generation_members(
                    self.point_ops.client,
                    pending_collection_name,
                    branch,
                    commit,
                )
                if source_tree is not None:
                    require_repository_source_tree_unchanged(
                        repo_path_obj,
                        source_tree,
                    )
                if not source_tree_sha256:
                    raise RuntimeError(
                        "repository source-tree identity is required to seal an index generation"
                    )
                manifest = build_generation_manifest_node(
                    workspace=workspace,
                    project=project,
                    branch=branch,
                    commit=commit,
                    member_count=len(members),
                    members_sha256=compute_generation_members_digest(members),
                    source_tree_sha256=source_tree_sha256,
                    index_include_patterns=include_patterns or (),
                    index_exclude_patterns=exclude_patterns or (),
                    identity_metadata=identity_metadata,
                )
                manifest_success, manifest_failed = (
                    self.point_ops.process_and_upsert_chunks(
                        [manifest],
                        pending_collection_name,
                        workspace,
                        project,
                        branch,
                        operation_id=operation_id,
                    )
                )
                if manifest_success != 1 or manifest_failed:
                    raise RuntimeError(
                        "repository generation manifest could not be persisted"
                    )
                generation_manifest_points = 1
                generation_manifest_sha256 = manifest.metadata[
                    "generation_manifest_sha256"
                ]

            logger.info(
                f"Streaming indexing complete: {document_count} files, "
                f"{successful_chunks}/{chunk_count} chunks indexed "
                f"({skipped_chunk_count} skipped across "
                f"{len(skipped_file_paths)} files)"
            )

            # Verify and perform atomic swap
            report_progress("verifying", "Verifying the pending vector collection", 94)
            pending_info = self.point_ops.client.get_collection(pending_collection_name)
            actual_point_count = int(pending_info.points_count or 0)
            expected_point_count = (
                preserved_point_count
                + successful_chunks
                + generation_manifest_points
            )
            if actual_point_count != expected_point_count:
                raise RuntimeError(
                    "Pending collection point count is incomplete: "
                    f"expected={expected_point_count}, actual={actual_point_count}"
                )

            target_branch_point_count = (
                self.branch_manager.get_branch_point_count(
                    pending_collection_name,
                    branch,
                )
            )
            expected_target_branch_points = (
                successful_chunks + generation_manifest_points
            )
            if target_branch_point_count != expected_target_branch_points:
                raise RuntimeError(
                    "Pending target-branch point count is incomplete: "
                    f"branch={branch}, expected={expected_target_branch_points}, "
                    f"actual={target_branch_point_count}"
                )

            if activation_guard is not None:
                activation_guard()
            observed_targets = self.collection_manager.read_alias_targets(
                activation_aliases
            )
            if observed_targets != activation_alias_targets:
                raise RuntimeError(
                    "Active RAG alias changed before pending activation"
                )

            activation_started = time.perf_counter()
            report_progress("activating", "Activating the completed vector collection", 97)
            old_targets = self._perform_atomic_swap(
                alias_name,
                pending_collection_name,
                activation_aliases,
            )
            logger.info(
                "RAG pending collection activated operation_id=%s collection=%s "
                "duration_ms=%s",
                operation_id,
                pending_collection_name,
                round((time.perf_counter() - activation_started) * 1000),
            )

            try:
                self.stats_manager.store_metadata(
                    workspace,
                    project,
                    branch,
                    commit,
                    document_count,
                    successful_chunks,
                )
            except Exception:
                self._rollback_atomic_swap(old_targets)
                raise

            old_target = old_targets.get(alias_name)
            # Exact aliases identify prior sealed revisions that can still be
            # selected by PR analysis. Their registry retention owns physical
            # cleanup; a replacement build must not delete them here.
            if (
                not seal_generation
                and old_target
                and old_target != pending_collection_name
            ):
                self.collection_manager.delete_collection(old_target)

        except Exception as e:
            logger.error(f"Indexing failed: {e}")
            self.collection_manager.delete_collection(pending_collection_name)
            raise
        finally:
            gc.collect()

        namespace = make_namespace(workspace, project, branch)
        logger.info(
            "RAG repository index completed operation_id=%s workspace=%s "
            "project=%s branch=%s files=%s chunks=%s reused=%s embedded=%s "
            "duration_ms=%s",
            operation_id,
            workspace,
            project,
            branch,
            document_count,
            successful_chunks,
            embedding_metrics["reused"],
            embedding_metrics["embedded"],
            round((time.perf_counter() - operation_started) * 1000),
        )
        report_progress(
            "complete",
            f"Indexed {document_count} files into {successful_chunks} chunks",
            100,
            document_count,
            indexedChunks=successful_chunks,
            estimatedChunks=successful_chunks,
            completedBatches=total_batches,
            totalBatches=total_batches,
            estimatedRemainingMs=0,
        )
        return IndexStats(
            namespace=namespace,
            document_count=document_count,
            chunk_count=successful_chunks,
            skipped_file_count=len(skipped_file_paths),
            skipped_chunk_count=skipped_chunk_count,
            last_updated=datetime.now(timezone.utc).isoformat(),
            workspace=workspace,
            project=project,
            branch=branch,
            generation_manifest_sha256=generation_manifest_sha256,
            source_tree_sha256=source_tree_sha256,
            collection_target=alias_name,
        )
    
    def _perform_atomic_swap(
            self,
            alias_name: str,
            pending_collection_name: str,
            activation_aliases: List[str],
    ) -> dict[str, Optional[str]]:
        """Activate a complete generation and its readable aliases together."""
        logger.info("Performing atomic alias swap...")
        old_targets = self.collection_manager.read_alias_targets(activation_aliases)
        self.collection_manager.atomic_assign_aliases(
            {alias: pending_collection_name for alias in activation_aliases}
        )
        return old_targets

    def _rollback_atomic_swap(self, old_targets: dict[str, Optional[str]]) -> None:
        """Restore every alias changed during a failed metadata publication."""
        self.collection_manager.atomic_assign_aliases(old_targets)


class FileOperations:
    """Apply rollback-safe file and neutral repository-graph updates."""

    accept_recoverable_repository_diagnostics = staticmethod(
        RepositoryIndexer.accept_recoverable_repository_diagnostics
    )
    
    def __init__(
        self,
        client,
        point_ops: PointOperations,
        collection_manager: CollectionManager,
        stats_manager: StatsManager,
        splitter,
        loader,
        plugin_catalog=None,
        plugin_runtime=None,
        plugin_selector=None,
        representation_fingerprint: Optional[str] = None,
    ):
        self.client = client
        self.point_ops = point_ops
        self.collection_manager = collection_manager
        self.stats_manager = stats_manager
        self.splitter = splitter
        self.loader = loader
        self.plugin_catalog = plugin_catalog
        self.plugin_runtime = plugin_runtime
        self.plugin_selector = plugin_selector
        self.representation_fingerprint = (
            representation_fingerprint
            or index_representation_fingerprint()
        )

    @staticmethod
    def _normalize_paths(file_paths: List[str]) -> List[str]:
        normalized = []
        for original in file_paths:
            path = original.replace("\\", "/") if isinstance(original, str) else ""
            segments = path.split("/")
            if (
                not path
                or path.startswith("/")
                or path.endswith("/")
                or any(segment in {"", ".", ".."} for segment in segments)
            ):
                raise ValueError(f"invalid repository-relative file path: {original!r}")
            normalized.append(path)
        return sorted(set(normalized))

    def _records_for_values(
        self,
        collection_name: str,
        branch: str,
        field: str,
        values,
    ):
        from ..repository_overlay import scroll_branch_points

        ordered = sorted(set(values))
        records = []
        for offset in range(0, len(ordered), 128):
            records.extend(scroll_branch_points(
                self.client,
                collection_name,
                branch,
                (FieldCondition(
                    key=field,
                    match=MatchAny(any=ordered[offset:offset + 128]),
                ),),
                with_vectors=True,
            ))
        return records

    @staticmethod
    def _as_point_struct(record) -> PointStruct:
        return PointStruct(
            id=record.id,
            vector=record.vector,
            payload=record.payload,
        )

    def _delete_point_ids(self, collection_name: str, point_ids) -> None:
        point_ids = list(point_ids)
        for offset in range(0, len(point_ids), 512):
            self.client.delete(
                collection_name=collection_name,
                points_selector=PointIdsList(
                    points=point_ids[offset:offset + 512],
                ),
            )

    def _restore_old_points(
        self,
        collection_name: str,
        old_points,
        new_only_ids,
    ) -> None:
        """Restore the exact pre-mutation point set after a failed replacement."""
        rollback_failures = []
        old_structs = [self._as_point_struct(point) for point in old_points.values()]
        for offset in range(0, len(old_structs), 128):
            try:
                self.client.upsert(
                    collection_name=collection_name,
                    points=old_structs[offset:offset + 128],
                    wait=True,
                )
            except Exception as exception:
                rollback_failures.append(exception)
        try:
            self._delete_point_ids(collection_name, new_only_ids)
        except Exception as exception:
            rollback_failures.append(exception)
        if rollback_failures:
            raise RuntimeError(
                "incremental index replacement failed and rollback was incomplete"
            ) from rollback_failures[0]

    def _replace_points(
        self,
        nodes,
        old_records,
        collection_name: str,
        workspace: str,
        project: str,
        branch: str,
        mutation_guard: Optional[Callable[[], None]] = None,
    ) -> int:
        """Upsert a prepared generation, delete stale IDs, and roll back on error."""
        old_records = list(old_records)
        old_points = {str(record.id): record for record in old_records}
        chunk_data = self.point_ops.prepare_chunks_for_embedding(
            nodes,
            workspace,
            project,
            branch,
        )
        embedding_metrics = {"reused": 0, "embedded": 0}
        new_points = self.point_ops.embed_and_create_points(
            chunk_data,
            reuse_records=old_points.values(),
            metrics=embedding_metrics,
        )
        logger.info(
            "Prepared incremental RAG generation collection=%s branch=%s "
            "points=%s reused=%s embedded=%s",
            collection_name,
            branch,
            len(new_points),
            embedding_metrics["reused"],
            embedding_metrics["embedded"],
        )
        return self._replace_prepared_points(
            new_points,
            old_records,
            collection_name,
            mutation_guard,
            allow_skipped=True,
        )

    def _replace_prepared_points(
        self,
        new_points,
        old_records,
        collection_name: str,
        mutation_guard: Optional[Callable[[], None]] = None,
        *,
        allow_skipped: bool,
    ) -> int:
        """Publish prepared points with rollback and explicit skip semantics."""
        old_points = {str(record.id): record for record in old_records}
        new_ids = {str(point.id) for point in new_points}
        old_ids = set(old_points)
        new_only_ids = [
            point.id for point in new_points if str(point.id) not in old_ids
        ]

        if mutation_guard is not None:
            mutation_guard()
        try:
            write_result = self.point_ops.upsert_points_detailed(
                collection_name,
                new_points,
            )
        except Exception:
            self._restore_old_points(
                collection_name,
                old_points,
                new_only_ids,
            )
            raise

        skipped_ids = {
            str(point.id) for point in write_result.skipped_points
        }
        if skipped_ids:
            if not allow_skipped:
                self._restore_old_points(
                    collection_name,
                    old_points,
                    new_only_ids,
                )
                raise RuntimeError(
                    "an exact index generation contains rejected points"
                )
            logger.warning(
                "Quarantining %s rejected points during incremental replacement",
                len(skipped_ids),
            )
        accepted_new_ids = new_ids - skipped_ids

        stale_ids = [
            record.id for point_id, record in old_points.items()
            if point_id not in accepted_new_ids
        ]
        try:
            if mutation_guard is not None:
                mutation_guard()
            self._delete_point_ids(collection_name, stale_ids)
        except Exception:
            self._restore_old_points(
                collection_name,
                old_points,
                new_only_ids,
            )
            raise
        return write_result.successful

    def replace_pr_overlay_generation(
        self,
        nodes,
        old_records,
        collection_name: str,
        workspace: str,
        project: str,
        point_id_branch: str,
        mutation_guard: Optional[Callable[[], None]] = None,
        *,
        pr_number: int,
        branch: str,
        base_branch: str,
        source_revision: str,
        base_revision: str,
        base_generation_manifest_sha256: str,
        generation_fingerprint: str,
        overlay_representation_fingerprint: str,
        identity_metadata,
    ) -> tuple[int, dict]:
        """Embed all PR members, seal them, and publish one complete set."""
        from ..pr_overlay_manifest import build_pr_overlay_manifest_node

        old_records = list(old_records)
        old_points = {str(record.id): record for record in old_records}
        chunk_data = self.point_ops.prepare_chunks_for_embedding(
            nodes, workspace, project, point_id_branch
        )
        new_points = self.point_ops.embed_and_create_points(
            chunk_data,
            reuse_records=old_points.values(),
        )
        new_ids = [point.id for point in new_points]
        if mutation_guard is not None:
            mutation_guard()
        successful, failed = self.point_ops.upsert_points(
            collection_name, new_points
        )
        if failed or successful != len(new_points):
            self._delete_point_ids(collection_name, new_ids)
            raise RuntimeError("PR overlay member write was incomplete")
        try:
            members = self.point_ops._seal_persisted_point_digests(
                collection_name, new_points
            )
            manifest_node, receipt = build_pr_overlay_manifest_node(
                workspace=workspace,
                project=project,
                pr_number=pr_number,
                branch=branch,
                base_branch=base_branch,
                source_revision=source_revision,
                base_revision=base_revision,
                base_generation_manifest_sha256=(
                    base_generation_manifest_sha256
                ),
                generation_fingerprint=generation_fingerprint,
                overlay_representation_fingerprint=(
                    overlay_representation_fingerprint
                ),
                members=members,
                identity_metadata=identity_metadata,
            )
            manifest_data = self.point_ops.prepare_chunks_for_embedding(
                [manifest_node], workspace, project, point_id_branch
            )
            manifest_points = self.point_ops.embed_and_create_points(
                manifest_data
            )
            manifest_success, manifest_failed = self.point_ops.upsert_points(
                collection_name, manifest_points
            )
            if manifest_success != 1 or manifest_failed:
                raise RuntimeError("PR overlay manifest write was incomplete")
        except Exception:
            self._delete_point_ids(
                collection_name,
                [*new_ids, *(point.id for point in locals().get("manifest_points", []))],
            )
            raise

        active_ids = {str(point.id) for point in (*new_points, *manifest_points)}
        stale_ids = [
            record.id for point_id, record in old_points.items()
            if point_id not in active_ids
        ]
        try:
            if mutation_guard is not None:
                mutation_guard()
            self._delete_point_ids(collection_name, stale_ids)
        except Exception:
            self._restore_old_points(
                collection_name,
                old_points,
                [*new_ids, *(point.id for point in manifest_points)],
            )
            raise
        return successful, receipt

    def _apply_change_set(
        self,
        updated_file_paths: List[str],
        deleted_file_paths: List[str],
        repo_base: Optional[str],
        workspace: str,
        project: str,
        branch: str,
        commit: Optional[str],
        collection_name: str,
        mutation_guard: Optional[Callable[[], None]] = None,
    ) -> IndexStats:
        from codecrow_plugins import (
            FileArtifact,
            FileDisposition,
            RepositoryAnalysis,
            RepositoryAnalysisMode,
            overlay_repository_facts,
        )
        from ..repository_overlay import (
            IncrementalIndexPreconditionError,
            affected_architecture_groups,
            architecture_group_from_payload,
            architecture_group_id,
            load_repository_facts,
            load_repository_snapshots,
            scroll_branch_points,
        )

        updated_paths = self._normalize_paths(updated_file_paths)
        deleted_paths = self._normalize_paths(deleted_file_paths)
        overlap = sorted(set(updated_paths).intersection(deleted_paths))
        if overlap:
            raise ValueError(
                "incremental change set cannot update and delete the same path: "
                + ", ".join(overlap[:10])
            )
        paths = sorted({*updated_paths, *deleted_paths})
        if not paths:
            return self.stats_manager.get_project_stats(
                workspace, project, collection_name
            )
        if updated_paths:
            if repo_base is None:
                raise ValueError(
                    "incremental updates require a changed-file repository root"
                )
            resolved_root = Path(repo_base).resolve(strict=True)
            missing_files = [
                path
                for path in updated_paths
                if not (resolved_root / path).resolve().is_file()
                or resolved_root not in (resolved_root / path).resolve().parents
            ]
            if missing_files:
                raise RuntimeError(
                    "incremental change set is incomplete; updated files are "
                    "missing from the pinned checkout: "
                    + ", ".join(missing_files[:10])
                )
        revision = commit or branch
        self.collection_manager.ensure_collection_exists(collection_name)
        observe_branch_representation(
            self.client,
            collection_name,
            branch,
            expected_fingerprint=self.representation_fingerprint,
        )

        (
            baseline_facts,
            facts_plugin_ids,
            _facts_fingerprint,
            _facts_descriptor_fingerprint,
            _facts_implementation_fingerprint,
        ) = load_repository_facts(
            self.client,
            collection_name,
            branch,
        )
        (
            snapshots,
            plugin_ids,
            _fingerprint,
            _stored_descriptor_fingerprint,
            _stored_implementation_fingerprint,
        ) = load_repository_snapshots(
            self.client,
            collection_name,
            branch,
        )
        capabilities = None
        implementation_fingerprint = "sha256:" + "0" * 64
        analysis = None
        repository_facts = None
        dispositions = {path: FileDisposition.FULL for path in paths}
        repository_analysis_plugins = ()
        if self.plugin_catalog is not None and self.plugin_selector is not None:
            if baseline_facts is None:
                raise IncrementalIndexPreconditionError(
                    f"indexed branch '{branch}' has no complete repository "
                    "detection facts; fully reindex the branch before applying "
                    "incremental updates"
                )
            if tuple(facts_plugin_ids) != tuple(plugin_ids):
                raise IncrementalIndexPreconditionError(
                    f"indexed branch '{branch}' has inconsistent plugin selection "
                    "state; fully reindex the branch before applying incremental "
                    "updates"
                )
            repository_facts = overlay_repository_facts(
                baseline_facts,
                repo_base,
                revision,
                updated_paths,
                deleted_paths,
                self.plugin_catalog.registry,
            )
            capabilities = self.plugin_selector.select(repository_facts)
            if capabilities.repository_plugins != tuple(plugin_ids):
                old_label = ", ".join(plugin_ids) or "generic fallback"
                new_label = (
                    ", ".join(capabilities.repository_plugins)
                    or "generic fallback"
                )
                raise IncrementalIndexPreconditionError(
                    f"incremental changes alter repository plugin selection "
                    f"from [{old_label}] to [{new_label}] for branch '{branch}'; "
                    "fully reindex the branch before applying the commit"
                )

        if capabilities is not None:
            if self.plugin_runtime is None or self.plugin_catalog is None:
                raise RuntimeError("repository plugins are unavailable")
            implementation_fingerprint = (
                self.plugin_catalog.implementation_fingerprint(
                    capabilities.repository_plugins
                )
            )
            repository_analysis_plugins = (
                self.plugin_runtime.repository_analysis_plugins(capabilities)
            )
            required_snapshot_plugins = set(repository_analysis_plugins)
            available_snapshot_plugins = {
                snapshot.plugin_id for snapshot in snapshots
            }
            missing_snapshot_plugins = sorted(
                required_snapshot_plugins - available_snapshot_plugins
            )
            if missing_snapshot_plugins:
                raise IncrementalIndexPreconditionError(
                    f"indexed branch '{branch}' is missing repository-analysis "
                    f"snapshots for {', '.join(missing_snapshot_plugins)}; "
                    "reindex the branch before applying an incremental update"
                )
            dispositions = {
                path: self.plugin_runtime.file_disposition(path, capabilities)
                for path in paths
            }

        active_updated_paths = [
            path for path in updated_paths
            if dispositions[path] not in {
                FileDisposition.EXCLUDED,
                FileDisposition.GENERATED,
            }
        ]
        active_deleted_paths = [
            path for path in deleted_paths
            if dispositions[path] not in {
                FileDisposition.EXCLUDED,
                FileDisposition.GENERATED,
            }
        ]
        documents = []
        if active_updated_paths:
            documents = self.loader.load_specific_files(
                file_paths=[Path(path) for path in active_updated_paths],
                repo_base=Path(repo_base),
                workspace=workspace,
                project=project,
                branch=branch,
                commit=revision,
            )

        documents_by_path = {
            document.metadata["path"]: document for document in documents
        }
        missing = sorted(
            path for path in active_updated_paths
            if path not in documents_by_path
        )
        if missing:
            logger.info(
                "Quarantining %s changed repository files that could not "
                "be loaded during incremental indexing: %s",
                len(missing),
                ", ".join(missing[:10]),
            )

        if repository_analysis_plugins:
            # A loader omission is not a deletion. Lock files, binary files,
            # oversized files, and locally excluded files can all be present
            # in the authoritative checkout while intentionally producing no
            # Document. Feeding those paths to repository plugins as
            # tombstones corrupts their restored state; dereferencing the
            # absent document also used to raise a raw KeyError (for example,
            # ``composer.lock``). Only successfully loaded updates and
            # provider-declared deletions participate in the plugin overlay.
            artifacts = tuple(sorted((
                *(
                    FileArtifact(
                        path=path,
                        content=documents_by_path[path].text,
                    )
                    for path in active_updated_paths
                    if path in documents_by_path
                ),
                *(
                    FileArtifact(path=path, content="", deleted=True)
                    for path in active_deleted_paths
                ),
            ), key=lambda artifact: artifact.path))
            handle = self.plugin_runtime.start_repository_analysis(
                capabilities,
                revision,
                snapshots=snapshots,
                mode=RepositoryAnalysisMode.PERSISTENT_INCREMENTAL,
                source_root=repository_facts.source_root if repository_facts else None,
            )
            handle.ingest(artifacts)
            analysis, diagnostics = handle.finish()
            self.accept_recoverable_repository_diagnostics(
                diagnostics,
                "incremental repository overlay",
            )

        semantic_documents = [
            document for document in documents
            if dispositions.get(document.metadata["path"], FileDisposition.FULL)
            is FileDisposition.FULL
        ]
        if semantic_documents:
            semantic_nodes, split_skipped_paths = (
                self.splitter.split_documents_resilient(
                    semantic_documents,
                    capabilities=capabilities,
                )
            )
            if split_skipped_paths:
                logger.warning(
                    "Quarantining %s changed files that failed semantic "
                    "parsing/enrichment: %s",
                    len(split_skipped_paths),
                    ", ".join(split_skipped_paths[:10]),
                )
        else:
            semantic_nodes = []
        identity_metadata = _plugin_identity_metadata(
            capabilities,
            implementation_fingerprint,
            self.representation_fingerprint,
        )
        for node in semantic_nodes:
            node.metadata.update(identity_metadata)

        # Preserve the last usable semantic and repository-graph
        # representations for updates which the loader quarantined. In
        # copy-on-write generations this also keeps the already copied points
        # intact. Real deletions and loaded updates still replace their prior
        # representations normally. Repository facts intentionally continue
        # to use the authoritative provider change set above.
        replacement_paths = sorted({
            *active_deleted_paths,
            *(
                path for path in active_updated_paths
                if path in documents_by_path
            ),
        })
        old_path_records = self._records_for_values(
            collection_name, branch, "path", replacement_paths
        )
        old_semantic_records = [
            record for record in old_path_records
            if not any((record.payload or {}).get(marker) for marker in (
                "architecture_context",
                "architecture_source",
                "repository_snapshot",
                "repository_facts_state",
            ))
        ]
        new_nodes = list(semantic_nodes)
        old_records = {str(record.id): record for record in old_semantic_records}

        if analysis is not None:
            impacted_old_nodes = self._records_for_values(
                collection_name,
                branch,
                "architecture_paths",
                replacement_paths,
            )
            impacted_old_nodes = [
                record for record in impacted_old_nodes
                if (record.payload or {}).get("architecture_context")
            ]
            old_groups = {
                architecture_group_from_payload(record.payload or {})
                for record in impacted_old_nodes
            }
            groups = old_groups | affected_architecture_groups(
                analysis,
                replacement_paths,
            )
            group_ids = {architecture_group_id(group) for group in groups}
            old_group_nodes = self._records_for_values(
                collection_name, branch, "architecture_group", group_ids
            )
            old_group_nodes.extend(impacted_old_nodes)

            architecture_nodes = RepositoryIndexer._architecture_nodes(
                analysis,
                capabilities,
                workspace,
                project,
                branch,
                revision,
                implementation_fingerprint,
                self.representation_fingerprint,
                groups=groups,
            )
            related_paths = set(replacement_paths)
            for record in old_group_nodes:
                related_paths.update(
                    (record.payload or {}).get("architecture_paths") or ()
                )
            for node in architecture_nodes:
                related_paths.update(node.metadata.get("architecture_paths") or ())

            old_context_records = [
                record for record in self._records_for_values(
                    collection_name, branch, "path", related_paths
                )
                if (record.payload or {}).get("architecture_source")
            ]
            affected_analysis = RepositoryAnalysis(contexts=tuple(
                context for context in analysis.contexts
                if context.path in related_paths
            ))
            context_nodes = RepositoryIndexer._repository_context_nodes(
                affected_analysis,
                capabilities,
                workspace,
                project,
                branch,
                revision,
                implementation_fingerprint,
                self.representation_fingerprint,
            )
            snapshot_nodes = RepositoryIndexer._snapshot_nodes(
                analysis,
                capabilities,
                workspace,
                project,
                branch,
                revision,
                implementation_fingerprint,
                self.representation_fingerprint,
            )
            old_snapshot_records = scroll_branch_points(
                self.client,
                collection_name,
                branch,
                (FieldCondition(
                    key="repository_snapshot",
                    match=MatchValue(value=True),
                ),),
                with_vectors=True,
            )
            new_nodes.extend((*architecture_nodes, *context_nodes, *snapshot_nodes))
            for record in (
                *old_group_nodes,
                *old_context_records,
                *old_snapshot_records,
            ):
                old_records[str(record.id)] = record

        if repository_facts is not None:
            facts_nodes = RepositoryIndexer._repository_facts_nodes(
                repository_facts,
                capabilities,
                workspace,
                project,
                branch,
                revision,
                implementation_fingerprint,
                self.representation_fingerprint,
            )
            old_facts_records = scroll_branch_points(
                self.client,
                collection_name,
                branch,
                (FieldCondition(
                    key="repository_facts_state",
                    match=MatchValue(value=True),
                ),),
                with_vectors=True,
            )
            new_nodes.extend(facts_nodes)
            for record in old_facts_records:
                old_records[str(record.id)] = record

        successful = self._replace_points(
            new_nodes,
            old_records.values(),
            collection_name,
            workspace,
            project,
            branch,
            mutation_guard,
        )
        logger.info(
            "Applied incremental branch generation for %s: %s paths, %s points",
            branch,
            len(paths),
            successful,
        )
        return self.stats_manager.get_project_stats(
            workspace, project, collection_name
        )
    
    def update_files(
        self,
        file_paths: List[str],
        repo_base: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        collection_name: str,
        mutation_guard: Optional[Callable[[], None]] = None,
    ) -> IndexStats:
        """Update files and every affected neutral repository-graph group."""
        logger.info(f"Updating {len(file_paths)} files in {workspace}/{project} for branch '{branch}'")
        return self._apply_change_set(
            file_paths,
            [],
            repo_base,
            workspace,
            project,
            branch,
            commit,
            collection_name,
            mutation_guard,
        )

    def delete_files(
        self,
        file_paths: List[str],
        workspace: str,
        project: str,
        branch: str,
        collection_name: str,
        commit: Optional[str] = None,
        mutation_guard: Optional[Callable[[], None]] = None,
    ) -> IndexStats:
        """Delete files and refresh every affected repository-graph group."""
        logger.info(f"Deleting {len(file_paths)} files from {workspace}/{project} branch '{branch}'")
        return self._apply_change_set(
            [],
            file_paths,
            None,
            workspace,
            project,
            branch,
            commit,
            collection_name,
            mutation_guard,
        )

    def apply_changes(
        self,
        updated_file_paths: List[str],
        deleted_file_paths: List[str],
        repo_base: Optional[str],
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        collection_name: str,
        mutation_guard: Optional[Callable[[], None]] = None,
    ) -> IndexStats:
        """Apply one complete commit change set through one rollback boundary."""
        logger.info(
            "Applying incremental change set to %s/%s branch '%s': "
            "%s updated, %s deleted",
            workspace,
            project,
            branch,
            len(updated_file_paths),
            len(deleted_file_paths),
        )
        return self._apply_change_set(
            updated_file_paths,
            deleted_file_paths,
            repo_base,
            workspace,
            project,
            branch,
            commit,
            collection_name,
            mutation_guard,
        )
