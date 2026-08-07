"""
Main RAG Index Manager.

Composes all index management components and provides the public API.
"""

import logging
import os
import hashlib
import json
import re
from typing import Callable, Optional, List

from llama_index.core import Settings
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

from ...models.config import RAGConfig, IndexStats
from ...utils.utils import make_namespace, make_project_namespace
from ..splitter import ASTCodeSplitter
from ..loader import DocumentLoader
from ..embedding_factory import create_embedding_model, get_embedding_model_info
from ..coordination import ProjectMutationCoordinator
from ..index_representation import (
    branch_splitter_kwargs,
    index_representation_fingerprint,
)
from ..pr_overlay_representation import (
    pr_overlay_representation_fingerprint,
)
from ..generation_manifest import (
    GENERATION_MANIFEST_PAYLOAD_KEY,
    GENERATION_MEMBER_DIGEST_PAYLOAD_KEY,
    build_generation_manifest_node,
    collect_generation_members,
    compute_generation_member_digest,
    compute_generation_members_digest,
)
from .. import revision_preflight
from ..repository_overlay import IncrementalIndexPreconditionError
from ..source_tree import (
    compute_repository_source_tree_sha256,
    verify_repository_source_tree,
)

from .collection_manager import CollectionManager
from .branch_manager import BranchManager
from .point_operations import PointOperations
from .stats_manager import StatsManager
from .indexer import RepositoryIndexer, FileOperations

logger = logging.getLogger(__name__)


def read_repository_revision_preflight(*args, **kwargs):
    """Patchable module boundary around strict generation verification."""
    return revision_preflight.read_repository_revision_preflight(
        *args, **kwargs
    )


def _config_int(config, name: str, default: int) -> int:
    value = getattr(config, name, default)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _config_float(config, name: str, default: float) -> float:
    value = getattr(config, name, default)
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


class RAGIndexManager:
    """Manage RAG indices for code repositories using Qdrant.
    
    This is the main entry point for all indexing operations.
    """

    def __init__(self, config: RAGConfig):
        self.config = config
        self._mutation_coordinator = ProjectMutationCoordinator(
            os.getenv("REDIS_URL", "redis://redis:6379/1"),
            lease_seconds=_config_int(
                config,
                "rag_mutation_lease_seconds",
                300,
            ),
            acquire_timeout_seconds=_config_float(
                config,
                "rag_mutation_acquire_timeout_seconds",
                5.0,
            ),
        )
        self.index_representation_fingerprint = (
            index_representation_fingerprint(config)
        )
        self.pr_overlay_representation_fingerprint = (
            pr_overlay_representation_fingerprint(config)
        )

        plugin_catalog = None
        plugin_runtime = None
        plugin_selector = None
        try:
            from codecrow_plugins import PluginRuntime, ProjectSelector
            from codecrow_plugins.bootstrap import discover_builtin_plugins

            plugin_catalog = discover_builtin_plugins()
            plugin_runtime = PluginRuntime(plugin_catalog)
            plugin_selector = ProjectSelector(plugin_catalog.registry)
            logger.info("Loaded plugins: %s", ", ".join(plugin_catalog.registry.ordered_ids))
        except ModuleNotFoundError as exception:
            if exception.name != "codecrow_plugins":
                raise
            logger.warning("Plugin package is not installed; using the generic RAG fallback")

        self.plugin_catalog = plugin_catalog
        self.plugin_runtime = plugin_runtime
        self.plugin_selector = plugin_selector

        # Qdrant client
        self.qdrant_client = QdrantClient(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key or None,
        )
        logger.info(f"Connected to Qdrant at {config.qdrant_url}")

        # Embedding model (supports Ollama and OpenRouter via factory)
        embed_info = get_embedding_model_info(config)
        logger.info(f"Using embedding provider: {embed_info['provider']} ({embed_info['type']})")
        logger.info(f"Embedding model: {embed_info['model']}, dimension: {embed_info['embedding_dim']}")
        
        self.embed_model = create_embedding_model(config, workload="index")

        # Global settings
        Settings.embed_model = self.embed_model
        Settings.chunk_size = config.chunk_size
        Settings.chunk_overlap = config.chunk_overlap

        # Splitter and loader
        logger.info("Using ASTCodeSplitter for code chunking (tree-sitter query-based)")
        self.splitter = ASTCodeSplitter(
            **branch_splitter_kwargs(config),
            plugin_runtime=plugin_runtime,
        )
        self.loader = DocumentLoader(config)

        # Component managers
        self._collection_manager = CollectionManager(
            self.qdrant_client, config.embedding_dim
        )
        self._branch_manager = BranchManager(self.qdrant_client)
        self._point_ops = PointOperations(
            self.qdrant_client,
            self.embed_model,
            batch_size=_config_int(config, "qdrant_upsert_batch_size", 128),
            embedding_batch_size=(
                _config_int(config, "openrouter_batch_size", 50)
                if str(config.embedding_provider).lower() == "openrouter"
                else 50
            ),
            max_embedding_workers=(
                _config_int(config, "openrouter_index_concurrency", 8)
                if str(config.embedding_provider).lower() == "openrouter"
                else 1
            ),
            embedding_dim=config.embedding_dim,
        )
        self._stats_manager = StatsManager(
            self.qdrant_client, config.qdrant_collection_prefix
        )
        
        # Higher-level operations
        self._indexer = RepositoryIndexer(
            config=config,
            collection_manager=self._collection_manager,
            branch_manager=self._branch_manager,
            point_ops=self._point_ops,
            stats_manager=self._stats_manager,
            splitter=self.splitter,
            loader=self.loader,
            plugin_catalog=plugin_catalog,
            plugin_runtime=plugin_runtime,
            plugin_selector=plugin_selector,
        )
        self._file_ops = FileOperations(
            client=self.qdrant_client,
            point_ops=self._point_ops,
            collection_manager=self._collection_manager,
            stats_manager=self._stats_manager,
            splitter=self.splitter,
            loader=self.loader,
            plugin_catalog=plugin_catalog,
            plugin_runtime=plugin_runtime,
            plugin_selector=plugin_selector,
            representation_fingerprint=(
                self.index_representation_fingerprint
            ),
        )

    # Collection naming

    def _get_project_collection_name(self, workspace: str, project: str) -> str:
        """Generate Qdrant collection name from workspace/project."""
        namespace = make_project_namespace(workspace, project)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _get_branch_operator_alias(
        self,
        workspace: str,
        project: str,
        branch: str,
    ) -> str:
        """Return a readable, collision-safe alias for one branch's head.

        Exact review context always uses the immutable generation target from
        the Java registry. This alias is an operator and legacy-integration
        pointer to the current published head of one branch.
        """
        legacy_alias = self._get_project_collection_name(workspace, project)
        raw_branch = branch.strip()
        readable = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_branch).lower()
        if not readable:
            readable = "branch"
        if readable != raw_branch.lower() or len(readable) > 64:
            readable = (
                f"{readable[:64]}_"
                f"{hashlib.sha256(raw_branch.encode('utf-8')).hexdigest()[:10]}"
            )
        return f"{legacy_alias}__{readable}"

    def _publication_aliases(
        self,
        workspace: str,
        project: str,
        branch: str,
        publish_branch_alias: bool,
        publish_legacy_project_alias: bool,
    ) -> List[str]:
        aliases: List[str] = []
        if publish_branch_alias:
            aliases.append(self._get_branch_operator_alias(workspace, project, branch))
        if publish_legacy_project_alias:
            aliases.append(self._get_project_collection_name(workspace, project))
        return list(dict.fromkeys(aliases))

    @staticmethod
    def _publication_scope(
        branch: str,
        publication_aliases: List[str],
    ) -> Optional[str]:
        """Return the mutable resource shared by one branch head.

        Exact target aliases are immutable and may be built independently.
        Once a build publishes a readable branch pointer, later builds of the
        same branch must serialize their final alias activation. Other branch
        scopes remain independent, so retained branches still index in
        parallel.
        """
        return f"branch-head:{branch}" if publication_aliases else None

    # Repository indexing

    def estimate_repository_size(
        self,
        repo_path: str,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None
    ) -> tuple[int, int]:
        """Estimate repository size (file count and chunk count)."""
        return self._indexer.estimate_repository_size(repo_path, include_patterns, exclude_patterns)

    def index_repository(
        self,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        preserve_other_branches: bool = False,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        source_tree_sha256: Optional[str] = None,
        collection_target: Optional[str] = None,
        publish_branch_alias: bool = False,
        publish_legacy_project_alias: bool = False,
        progress_callback: Optional[Callable[[dict], None]] = None,
    ) -> IndexStats:
        """Index entire repository for a branch using atomic swap strategy."""
        alias_name = collection_target or self._get_project_collection_name(
            workspace, project
        )
        expected_source_tree = (
            source_tree_sha256
            or compute_repository_source_tree_sha256(repo_path)
        )
        source_tree = verify_repository_source_tree(
            repo_path,
            commit,
            expected_source_tree,
        )
        publication_aliases = self._publication_aliases(
            workspace,
            project,
            branch,
            publish_branch_alias,
            publish_legacy_project_alias,
        )
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "full-index",
            collection_target=collection_target,
            publication_scope=self._publication_scope(branch, publication_aliases),
        ) as lease:
            return self._indexer.index_repository(
                repo_path=repo_path,
                workspace=workspace,
                project=project,
                branch=branch,
                commit=commit,
                alias_name=alias_name,
                preserve_other_branches=preserve_other_branches,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                source_tree_sha256=source_tree.tree_sha256,
                source_tree=source_tree,
                seal_generation=collection_target is not None,
                publication_aliases=publication_aliases,
                operation_id=lease.token,
                activation_guard=lease.assert_owned,
                progress_callback=progress_callback,
            )

    def get_revision_preflight(
        self,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        *,
        collection_target: Optional[str] = None,
    ):
        """Verify one immutable revision in an explicitly selected target."""
        target = collection_target or self._get_project_collection_name(
            workspace, project
        )
        physical = self._collection_manager.resolve_collection_target(target)
        if physical is None:
            return None
        result = read_repository_revision_preflight(
            self.qdrant_client,
            physical,
            branch,
            commit,
        )
        if result is None:
            return None
        if (
            result.get("workspace") != workspace
            or result.get("project") != project
        ):
            raise IncrementalIndexPreconditionError(
                "repository generation coordinates do not match the requested tenant"
            )
        return result

    def publish_generation_aliases(
        self,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        collection_target: str,
        publish_branch_alias: bool = True,
        publish_legacy_project_alias: bool = False,
    ) -> List[str]:
        """Repair operator aliases after proving one sealed generation identity.

        Normal indexing publishes these pointers atomically with its immutable
        target alias. This idempotent path exists only for generations created
        before that contract was introduced or when Qdrant has been restored.
        """
        aliases = self._publication_aliases(
            workspace,
            project,
            branch,
            publish_branch_alias,
            publish_legacy_project_alias,
        )
        if not aliases:
            return []
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "publish-generation-aliases",
            collection_target=collection_target,
            publication_scope=self._publication_scope(branch, aliases),
        ) as lease:
            physical = self._collection_manager.resolve_collection_target(
                collection_target
            )
            if physical is None:
                raise IncrementalIndexPreconditionError(
                    "repository generation is unavailable for alias publication"
                )
            receipt = read_repository_revision_preflight(
                self.qdrant_client,
                physical,
                branch,
                commit,
            )
            if receipt is None or any(receipt.get(key) != value for key, value in (
                ("workspace", workspace),
                ("project", project),
                ("branch", branch),
                ("commit", commit),
            )):
                raise IncrementalIndexPreconditionError(
                    "repository generation does not match the requested alias coordinates"
                )
            lease.assert_owned()
            self._collection_manager.atomic_assign_aliases(
                {alias: physical for alias in aliases}
            )
        return aliases

    # File operations

    def update_files(
        self,
        file_paths: List[str],
        repo_base: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str
    ) -> IndexStats:
        """Update specific files in the index (Delete Old -> Insert New)."""
        collection_name = self._get_project_collection_name(workspace, project)
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "update-files",
        ) as lease:
            return self._file_ops.update_files(
                file_paths=file_paths,
                repo_base=repo_base,
                workspace=workspace,
                project=project,
                branch=branch,
                commit=commit,
                collection_name=collection_name,
                mutation_guard=lease.assert_owned,
            )

    def delete_files(
        self,
        file_paths: List[str],
        workspace: str,
        project: str,
        branch: str,
        commit: Optional[str] = None,
    ) -> IndexStats:
        """Delete specific files from the index for a specific branch."""
        collection_name = self._get_project_collection_name(workspace, project)
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "delete-files",
        ) as lease:
            return self._file_ops.delete_files(
                file_paths=file_paths,
                workspace=workspace,
                project=project,
                branch=branch,
                collection_name=collection_name,
                commit=commit,
                mutation_guard=lease.assert_owned,
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
    ) -> IndexStats:
        """Apply a complete commit change set through one RAG mutation."""
        collection_name = self._get_project_collection_name(workspace, project)
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "apply-changes",
        ) as lease:
            return self._file_ops.apply_changes(
                updated_file_paths=updated_file_paths,
                deleted_file_paths=deleted_file_paths,
                repo_base=repo_base,
                workspace=workspace,
                project=project,
                branch=branch,
                commit=commit,
                collection_name=collection_name,
                mutation_guard=lease.assert_owned,
            )

    def _rebind_repository_facts_revision(
        self,
        collection_name: str,
        branch: str,
        commit: str,
    ) -> None:
        """Rebind the copied neutral inventory before applying its delta."""
        points = []
        offset = None
        while True:
            batch, offset = self.qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(must=[
                    FieldCondition(key="branch", match=MatchValue(value=branch)),
                    FieldCondition(
                        key="repository_facts_state",
                        match=MatchValue(value=True),
                    ),
                ]),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            points.extend(batch)
            if offset is None:
                break
        if not points:
            raise IncrementalIndexPreconditionError(
                "source generation has no repository detection facts"
            )
        ordered = sorted(
            points, key=lambda point: (point.payload or {}).get("facts_part", -1)
        )
        content = "".join((point.payload or {}).get("text", "") for point in ordered)
        decoded = json.loads(content)
        decoded["revision"] = commit
        rebound = json.dumps(
            decoded,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        parts = [
            rebound[index:index + 400_000]
            for index in range(0, len(rebound), 400_000)
        ]
        if len(parts) != len(ordered):
            raise IncrementalIndexPreconditionError(
                "repository facts cannot be rebound incrementally; fully reindex"
            )
        digest = hashlib.sha256(rebound.encode("utf-8")).hexdigest()
        replacements = []
        for index, (point, text_part) in enumerate(zip(ordered, parts)):
            payload = dict(point.payload or {})
            payload.update({
                "commit": commit,
                "text": text_part,
                "facts_part": index,
                "facts_parts": len(parts),
                "facts_content_sha256": digest,
            })
            payload[GENERATION_MEMBER_DIGEST_PAYLOAD_KEY] = (
                compute_generation_member_digest(point.id, payload, point.vector)
            )
            replacements.append(PointStruct(
                id=point.id, vector=point.vector, payload=payload
            ))
        self.qdrant_client.upsert(
            collection_name=collection_name,
            points=replacements,
            wait=True,
        )

    def advance_generation(
        self,
        source_collection_target: str,
        target_collection_target: str,
        source_commit: str,
        source_tree_sha256: str,
        updated_file_paths: List[str],
        deleted_file_paths: List[str],
        repo_base: Optional[str],
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        publish_branch_alias: bool = False,
        publish_legacy_project_alias: bool = False,
    ) -> IndexStats:
        """Copy, mutate, seal, and atomically publish one exact generation."""
        publication_aliases = self._publication_aliases(
            workspace,
            project,
            branch,
            publish_branch_alias,
            publish_legacy_project_alias,
        )
        activation_aliases = list(dict.fromkeys(
            [target_collection_target, *publication_aliases]
        ))
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "advance-generation",
            collection_target=target_collection_target,
            publication_scope=self._publication_scope(
                branch,
                publication_aliases,
            ),
        ) as lease:
            source_physical = self._collection_manager.resolve_collection_target(
                source_collection_target
            )
            if source_physical is None:
                raise IncrementalIndexPreconditionError(
                    "source repository generation is unavailable"
                )
            source_receipt = read_repository_revision_preflight(
                self.qdrant_client,
                source_physical,
                branch,
                source_commit,
            )
            if source_receipt is None:
                raise IncrementalIndexPreconditionError(
                    "source repository generation is not sealed"
                )

            target_physical = self._collection_manager.resolve_collection_target(
                target_collection_target
            )
            if target_physical is not None:
                existing = read_repository_revision_preflight(
                    self.qdrant_client,
                    target_physical,
                    branch,
                    commit,
                )
                if existing is None:
                    raise IncrementalIndexPreconditionError(
                        "target collection exists without the requested sealed generation"
                    )
                stats = self._stats_manager.get_branch_stats(
                    workspace, project, branch, target_physical
                )
                if publication_aliases:
                    self._collection_manager.atomic_assign_aliases(
                        {alias: target_physical for alias in publication_aliases}
                    )
                if hasattr(stats, "model_copy"):
                    return stats.model_copy(update={
                        "generation_manifest_sha256": existing[
                            "generation_manifest_sha256"
                        ],
                        "source_tree_sha256": existing["source_tree_sha256"],
                        "collection_target": target_collection_target,
                    })
                return stats

            if repo_base is None:
                raise IncrementalIndexPreconditionError(
                    "target repository snapshot is required for generation advance"
                )
            observed_source_tree_sha256 = (
                compute_repository_source_tree_sha256(repo_base)
            )
            if observed_source_tree_sha256 != source_tree_sha256:
                raise IncrementalIndexPreconditionError(
                    "target repository source tree does not match its attestation"
                )

            pending = self._collection_manager.create_pending_collection(
                target_collection_target,
                operation_id=lease.token,
            )
            activation_alias_targets = self._collection_manager.read_alias_targets(
                activation_aliases
            )
            activated = False
            try:
                offset = None
                copied = 0
                while True:
                    points, offset = self.qdrant_client.scroll(
                        collection_name=source_physical,
                        limit=256,
                        offset=offset,
                        with_payload=True,
                        with_vectors=True,
                    )
                    batch = []
                    for point in points:
                        payload = dict(point.payload or {})
                        if payload.get(GENERATION_MANIFEST_PAYLOAD_KEY) is True:
                            continue
                        if any(payload.get(key) != value for key, value in (
                            ("workspace", workspace),
                            ("project", project),
                            ("branch", branch),
                            ("commit", source_commit),
                        )):
                            raise IncrementalIndexPreconditionError(
                                "source generation contains points outside its tenant or revision"
                            )
                        payload["commit"] = commit
                        payload[GENERATION_MEMBER_DIGEST_PAYLOAD_KEY] = (
                            compute_generation_member_digest(
                                point.id, payload, point.vector
                            )
                        )
                        batch.append(PointStruct(
                            id=point.id,
                            vector=point.vector,
                            payload=payload,
                        ))
                    if batch:
                        self.qdrant_client.upsert(
                            collection_name=pending,
                            points=batch,
                            wait=True,
                        )
                        copied += len(batch)
                    if offset is None:
                        break
                if copied < 1:
                    raise IncrementalIndexPreconditionError(
                        "source generation has no repository members"
                    )

                self._rebind_repository_facts_revision(
                    pending, branch, commit
                )

                self._file_ops.apply_changes(
                    updated_file_paths=updated_file_paths,
                    deleted_file_paths=deleted_file_paths,
                    repo_base=repo_base,
                    workspace=workspace,
                    project=project,
                    branch=branch,
                    commit=commit,
                    collection_name=pending,
                    mutation_guard=lease.assert_owned,
                )

                members = collect_generation_members(
                    self.qdrant_client, pending, branch, commit
                )
                identity = {
                    "plugin_ids": source_receipt["plugin_ids"],
                    "plugin_fingerprint": source_receipt["plugin_fingerprint"],
                    "plugin_descriptor_fingerprint": source_receipt[
                        "plugin_descriptor_fingerprint"
                    ],
                    "plugin_implementation_fingerprint": source_receipt[
                        "plugin_implementation_fingerprint"
                    ],
                    "index_representation_fingerprint": source_receipt[
                        "index_representation_fingerprint"
                    ],
                }
                manifest = build_generation_manifest_node(
                    workspace=workspace,
                    project=project,
                    branch=branch,
                    commit=commit,
                    member_count=len(members),
                    members_sha256=compute_generation_members_digest(members),
                    source_tree_sha256=source_tree_sha256,
                    index_include_patterns=source_receipt[
                        "index_include_patterns"
                    ],
                    index_exclude_patterns=source_receipt[
                        "index_exclude_patterns"
                    ],
                    identity_metadata=identity,
                )
                success, failed = self._point_ops.process_and_upsert_chunks(
                    [manifest], pending, workspace, project, branch
                )
                if success != 1 or failed:
                    raise RuntimeError("target generation manifest was not persisted")
                lease.assert_owned()
                if self._collection_manager.read_alias_targets(
                        activation_aliases) != activation_alias_targets:
                    raise RuntimeError(
                        "Active RAG alias changed before pending activation"
                    )
                self._collection_manager.atomic_assign_aliases(
                    {alias: pending for alias in activation_aliases}
                )
                activated = True
                stats = self._stats_manager.get_branch_stats(
                    workspace, project, branch, pending
                )
                return stats.model_copy(update={
                    "generation_manifest_sha256": manifest.metadata[
                        "generation_manifest_sha256"
                    ],
                    "source_tree_sha256": source_tree_sha256,
                    "collection_target": target_collection_target,
                })
            finally:
                if not activated:
                    self._collection_manager.delete_collection(pending)

    # Branch operations

    def delete_branch(
        self,
        workspace: str,
        project: str,
        branch: str,
        collection_target: Optional[str] = None,
    ) -> bool:
        """Delete all points for a specific branch from the project collection."""
        if collection_target:
            return self.delete_collection_target(
                workspace, project, branch, collection_target
            )
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "delete-branch",
        ) as lease:
            collection_name = self._get_project_collection_name(workspace, project)
            if not self._collection_manager.collection_exists(collection_name):
                if not self._collection_manager.alias_exists(collection_name):
                    logger.warning(f"Collection {collection_name} does not exist")
                    return False

            lease.assert_owned()
            return self._branch_manager.delete_branch_points(collection_name, branch)

    def delete_collection_target(
        self,
        workspace: str,
        project: str,
        branch: str,
        collection_target: str,
    ) -> bool:
        """Delete one exact generation after proving its tenant ownership."""
        with self._mutation_coordinator.acquire(
            workspace, project, "delete-generation"
        ) as lease:
            physical = self._collection_manager.resolve_collection_target(
                collection_target
            )
            if physical is None:
                return False
            offset = None
            point_count = 0
            while True:
                points, offset = self.qdrant_client.scroll(
                    collection_name=physical,
                    limit=256,
                    offset=offset,
                    with_payload=["workspace", "project", "branch"],
                    with_vectors=False,
                )
                point_count += len(points)
                for point in points:
                    payload = point.payload or {}
                    if any(payload.get(key) != value for key, value in (
                        ("workspace", workspace),
                        ("project", project),
                        ("branch", branch),
                    )):
                        raise IncrementalIndexPreconditionError(
                            "collection target does not belong to the requested tenant branch"
                        )
                if offset is None:
                    break
            if point_count == 0:
                raise IncrementalIndexPreconditionError(
                    "empty collection target cannot be ownership-verified"
                )
            lease.assert_owned()
            if self._collection_manager.alias_exists(collection_target):
                if not self._collection_manager.delete_alias(collection_target):
                    return False
            return self._collection_manager.delete_collection(physical)

    def get_branch_point_count(self, workspace: str, project: str, branch: str) -> int:
        """Get the number of points for a specific branch."""
        collection_name = self._get_project_collection_name(workspace, project)
        
        if not self._collection_manager.collection_exists(collection_name):
            if not self._collection_manager.alias_exists(collection_name):
                return 0

        return self._branch_manager.get_branch_point_count(collection_name, branch)

    def get_indexed_branches(self, workspace: str, project: str) -> List[str]:
        """Get list of branches that have points in the collection."""
        collection_name = self._get_project_collection_name(workspace, project)
        
        if not self._collection_manager.collection_exists(collection_name):
            if not self._collection_manager.alias_exists(collection_name):
                return []

        return self._branch_manager.get_indexed_branches(collection_name)

    # Index management

    def delete_index(self, workspace: str, project: str, branch: str):
        """Delete branch data from project index."""
        if branch and branch != "*":
            self.delete_branch(workspace, project, branch)
        else:
            self.delete_project_index(workspace, project)

    def delete_project_index(self, workspace: str, project: str):
        """Delete entire project collection (all branches)."""
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "delete-project-index",
        ) as lease:
            collection_name = self._get_project_collection_name(workspace, project)
            namespace = make_project_namespace(workspace, project)

            logger.info(f"Deleting entire project index for {namespace}")

            # Coordination failures must escape to the HTTP boundary. The
            # legacy best-effort Qdrant cleanup below must not make a lost
            # lease look like a successful project deletion.
            lease.assert_owned()
            try:
                if self._collection_manager.alias_exists(collection_name):
                    actual_collection = self._collection_manager.resolve_alias(collection_name)
                    self._collection_manager.delete_alias(collection_name)
                    if actual_collection:
                        self._collection_manager.delete_collection(actual_collection)
                else:
                    self._collection_manager.delete_collection(collection_name)
                logger.info(f"Deleted Qdrant collection: {collection_name}")
            except Exception as e:
                logger.warning(f"Failed to delete Qdrant collection: {e}")

    def cleanup_expired_pending_collections(self) -> int:
        """Remove only old pending collections without a live operation lease."""
        return self._collection_manager.cleanup_expired_pending_collections(
            is_operation_active=self._mutation_coordinator.is_operation_active,
        )

    def project_mutation(self, workspace: str, project: str, operation: str):
        """Return the shared mutation boundary for auxiliary index endpoints."""
        return self._mutation_coordinator.acquire(workspace, project, operation)

    def close(self) -> None:
        self._mutation_coordinator.close()

    # Statistics

    def _get_index_stats(self, workspace: str, project: str, branch: str) -> IndexStats:
        """Get statistics about a branch index (backward compatibility)."""
        return self._get_branch_index_stats(workspace, project, branch)

    def _get_branch_index_stats(self, workspace: str, project: str, branch: str) -> IndexStats:
        """Get statistics about a specific branch within a project collection."""
        collection_name = self._get_project_collection_name(workspace, project)
        return self._stats_manager.get_branch_stats(
            workspace, project, branch, collection_name
        )

    def _get_project_index_stats(self, workspace: str, project: str) -> IndexStats:
        """Get statistics about a project's index (all branches combined)."""
        collection_name = self._get_project_collection_name(workspace, project)
        return self._stats_manager.get_project_stats(workspace, project, collection_name)

    def list_indices(self) -> List[IndexStats]:
        """List all project indices with branch breakdown."""
        return self._stats_manager.list_all_indices(
            self._collection_manager.alias_exists
        )

    # Legacy/compatibility methods
    
    def _ensure_collection_exists(self, collection_name: str):
        """Ensure Qdrant collection exists (legacy compatibility)."""
        self._collection_manager.ensure_collection_exists(collection_name)

    def _alias_exists(self, alias_name: str) -> bool:
        """Check if an alias exists (legacy compatibility)."""
        return self._collection_manager.alias_exists(alias_name)

    def _resolve_alias_to_collection(self, alias_name: str) -> Optional[str]:
        """Resolve an alias to its collection (legacy compatibility)."""
        return self._collection_manager.resolve_alias(alias_name)

    def _generate_point_id(
        self,
        workspace: str,
        project: str,
        branch: str,
        path: str,
        chunk_index: int
    ) -> str:
        """Generate deterministic point ID (legacy compatibility)."""
        return PointOperations.generate_point_id(
            workspace, project, branch, path, chunk_index
        )
