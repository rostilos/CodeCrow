"""
Tests for rag_pipeline.api.routers.pr — PR file indexing endpoints.

Covers:
- index_pr_files (full flow, empty replacement, deleted files, error handling)
- delete_pr_files (success, collection not found, error)
"""
import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from fastapi import HTTPException


DESCRIPTOR_FINGERPRINT = "sha256:" + "1" * 64
IMPLEMENTATION_FINGERPRINT = "sha256:" + "2" * 64
REPRESENTATION_FINGERPRINT = "sha256:" + "3" * 64
OVERLAY_REPRESENTATION_FINGERPRINT = "sha256:" + "4" * 64
BASE_GENERATION_MANIFEST_SHA256 = "5" * 64
OVERLAY_GENERATION_MANIFEST_SHA256 = "6" * 64
BASE_PLUGIN_FINGERPRINT = "sha256:" + "7" * 64


@pytest.fixture(autouse=True)
def _stable_index_representation(monkeypatch):
    monkeypatch.setattr(
        "rag_pipeline.api.routers.pr.observe_branch_representation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "rag_pipeline.api.routers.pr.read_pr_overlay_generation",
        lambda *_args, **kwargs: (
            {
                "overlay_generation_member_count": 0,
                "overlay_generation_members_sha256": "8" * 64,
                "overlay_generation_manifest_sha256": kwargs[
                    "expected_manifest_sha256"
                ],
            }
            if kwargs.get("expected_manifest_sha256")
            else None
        ),
    )


def _base_receipt(manifest=BASE_GENERATION_MANIFEST_SHA256):
    return {
        "generation_manifest_sha256": manifest,
        "_collection_target": "rag_ws__proj_active",
        "plugin_fingerprint": BASE_PLUGIN_FINGERPRINT,
        "plugin_descriptor_fingerprint": DESCRIPTOR_FINGERPRINT,
        "plugin_implementation_fingerprint": IMPLEMENTATION_FINGERPRINT,
        "index_representation_fingerprint": REPRESENTATION_FINGERPRINT,
    }


def _make_index_manager():
    im = MagicMock()
    mutation_context = MagicMock()
    mutation_context.__enter__.return_value = SimpleNamespace(
        assert_owned=MagicMock()
    )
    im.project_mutation.return_value = mutation_context
    im.index_representation_fingerprint = REPRESENTATION_FINGERPRINT
    im.pr_overlay_representation_fingerprint = (
        OVERLAY_REPRESENTATION_FINGERPRINT
    )
    im._get_project_collection_name.return_value = "rag_ws__proj"
    im._collection_manager.collection_exists.return_value = True
    im._collection_manager.resolve_collection_target.return_value = (
        "rag_ws__proj_active"
    )
    im.splitter.split_documents.return_value = []
    im.splitter.split_documents_resilient.side_effect = (
        lambda documents, capabilities=None: (
            im.splitter.split_documents(
                documents,
                capabilities=capabilities,
            ),
            (),
        )
    )
    im._point_ops.embed_and_create_points.return_value = []
    im._point_ops.upsert_points.return_value = (0, 0)
    im._point_ops.process_and_upsert_chunks.return_value = (0, 0)
    im.qdrant_client.scroll.return_value = ([], None)
    im.get_revision_preflight.return_value = _base_receipt()
    im._file_ops._replace_points.side_effect = (
        lambda nodes, *_args: len(nodes)
    )
    im._file_ops.replace_pr_overlay_generation.side_effect = (
        lambda nodes, *_args, **_kwargs: (
            len(nodes),
            {
                "overlay_generation_member_count": 0,
                "overlay_generation_members_sha256": "8" * 64,
                "overlay_generation_manifest_sha256": (
                    OVERLAY_GENERATION_MANIFEST_SHA256
                ),
            },
        )
    )
    im.plugin_catalog.registry.resolve.side_effect = lambda plugin_ids: [
        SimpleNamespace(id=plugin_id) for plugin_id in dict.fromkeys(plugin_ids)
    ]
    im.plugin_catalog.registry.fingerprint_for.return_value = (
        DESCRIPTOR_FINGERPRINT
    )
    im.plugin_catalog.implementation_fingerprint.return_value = (
        IMPLEMENTATION_FINGERPRINT
    )
    return im


def _request(files):
    return SimpleNamespace(
        workspace="ws",
        project="proj",
        pr_number=42,
        branch="feat",
        base_branch="main",
        source_revision="head-commit",
        base_revision="base-commit",
        repository_plugins=[],
        plugin_detection_evidence={},
        plugin_fingerprint="sha256:" + "0" * 64,
        plugin_descriptor_fingerprint=DESCRIPTOR_FINGERPRINT,
        files=files,
    )


def _capabilities(*plugin_ids, fingerprint="sha256:capabilities"):
    return SimpleNamespace(
        repository_plugins=tuple(plugin_ids),
        file_plugins={},
        detection_evidence={
            plugin_id: (f"fixture:{plugin_id}",)
            for plugin_id in plugin_ids
        },
        unavailable_capabilities=(),
        fingerprint=fingerprint,
        descriptor_fingerprint=DESCRIPTOR_FINGERPRINT,
    )


def test_same_pr_overlay_mutations_are_serialized():
    from rag_pipeline.api.routers.pr import _pr_overlay_lock

    first_entered = Event()
    release_first = Event()
    second_attempted = Event()
    second_entered = Event()

    def first():
        with _pr_overlay_lock("ws", "project", 42):
            first_entered.set()
            assert release_first.wait(2)

    def second():
        second_attempted.set()
        with _pr_overlay_lock("ws", "project", 42):
            second_entered.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first)
        assert first_entered.wait(1)
        second_future = executor.submit(second)
        assert second_attempted.wait(1)
        assert not second_entered.wait(0.1)
        release_first.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert second_entered.is_set()


# ─────────────────────────────────────────────────────────────
# index_pr_files
# ─────────────────────────────────────────────────────────────
class TestIndexPRFiles:

    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_base_generation_change_never_publishes_overlay(
        self,
        mock_get,
        mock_load_snapshots,
    ):
        im = _make_index_manager()
        im.get_revision_preflight.side_effect = [
            _base_receipt(),
            _base_receipt("9" * 64),
        ]
        mock_get.return_value = im
        mock_load_snapshots.return_value = ([], [], None, None, None)
        request = _request([
            SimpleNamespace(
                path="src/Foo.java",
                content="public class Foo {}",
                change_type="MODIFIED",
            ),
        ])

        from rag_pipeline.api.routers.pr import index_pr_files

        with pytest.raises(HTTPException) as exception:
            index_pr_files(request)

        assert exception.value.status_code == 409
        assert "generation changed" in exception.value.detail
        im._file_ops._replace_points.assert_not_called()

    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_identical_complete_generation_is_reused_without_embedding(
        self,
        mock_get,
        mock_load_snapshots,
        monkeypatch,
    ):
        from rag_pipeline.core.pr_overlay_identity import (
            ZERO_FINGERPRINT,
            pr_overlay_generation_fingerprint,
        )

        file_info = SimpleNamespace(
            path="src/Foo.java",
            content="public class Foo {}",
            change_type="MODIFIED",
        )
        related_file = SimpleNamespace(
            path="src/Bar.java",
            content="public class Bar {}",
            change_type="MODIFIED",
        )
        req = _request([file_info, related_file])
        generation_fingerprint = pr_overlay_generation_fingerprint(
            workspace=req.workspace,
            project=req.project,
            pr_number=req.pr_number,
            branch=req.branch,
            base_branch=req.base_branch,
            source_revision=req.source_revision,
            base_revision=req.base_revision,
            base_generation_manifest_sha256=(
                BASE_GENERATION_MANIFEST_SHA256
            ),
            files=req.files,
            requested_plugin_ids=(),
            repository_plugin_ids=(),
            request_plugin_fingerprint=req.plugin_fingerprint,
            target_plugin_fingerprint=ZERO_FINGERPRINT,
            capability_fingerprint=ZERO_FINGERPRINT,
            descriptor_fingerprint=ZERO_FINGERPRINT,
            implementation_fingerprint=ZERO_FINGERPRINT,
            index_representation_fingerprint=REPRESENTATION_FINGERPRINT,
            pr_overlay_representation_fingerprint=(
                OVERLAY_REPRESENTATION_FINGERPRINT
            ),
            snapshots=(),
        )
        existing = SimpleNamespace(payload={
            "pr_generation_fingerprint": generation_fingerprint,
            "architecture_context": True,
            "plugin_graph_facts": [{
                "kind": "java-reference",
                "path": "src/Foo.java",
                "related_paths": ["src/Bar.java"],
            }],
        })
        im = _make_index_manager()
        im.qdrant_client.scroll.return_value = ([existing], None)
        mock_get.return_value = im
        mock_load_snapshots.return_value = ((), (), None, None, None)
        monkeypatch.setattr(
            "rag_pipeline.api.routers.pr.read_pr_overlay_generation",
            lambda *_args, **_kwargs: {
                "overlay_generation_member_count": 1,
                "overlay_generation_members_sha256": "8" * 64,
                "overlay_generation_manifest_sha256": (
                    OVERLAY_GENERATION_MANIFEST_SHA256
                ),
            },
        )

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(req)

        assert result["status"] == "reused"
        assert result["chunks_indexed"] == 1
        assert result["review_groups"] == [["src/Bar.java", "src/Foo.java"]]
        im.splitter.split_documents.assert_not_called()
        im._file_ops._replace_points.assert_not_called()

    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_changed_base_revision_replaces_existing_generation(
        self,
        mock_get,
        mock_load_snapshots,
    ):
        existing = SimpleNamespace(payload={
            "pr_generation_fingerprint": "sha256:" + "9" * 64,
        })
        im = _make_index_manager()
        im.qdrant_client.scroll.return_value = ([existing], None)
        mock_get.return_value = im
        mock_load_snapshots.return_value = ((), (), None, None, None)
        req = _request([])

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(req)

        assert result["status"] == "indexed"
        im._file_ops.replace_pr_overlay_generation.assert_called_once()
        replacement_call = (
            im._file_ops.replace_pr_overlay_generation.call_args
        )
        assert replacement_call.args[5] == (
            "__pr__/42/feat/"
            + replacement_call.kwargs["generation_fingerprint"]
        )

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_success_with_files(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im

        # Simulate chunks after splitting
        mock_chunk = MagicMock()
        mock_chunk.metadata = {"path": "src/Foo.java"}
        im.splitter.split_documents.return_value = [mock_chunk]

        from rag_pipeline.api.routers.pr import index_pr_files

        file_info = MagicMock()
        file_info.content = "public class Foo {}"
        file_info.path = "src/Foo.java"
        file_info.change_type = "ADDED"

        req = _request([file_info])

        result = index_pr_files(req)
        assert result["status"] == "indexed"
        assert result["pr_number"] == 42
        assert result["files_processed"] == 1
        assert result["chunks_indexed"] == 1
        assert mock_chunk.metadata["pr_generation_fingerprint"].startswith(
            "sha256:"
        )
        assert mock_chunk.metadata["pr_source_revision"] == "head-commit"
        assert mock_chunk.metadata["pr_base_revision"] == "base-commit"
        assert (
            mock_chunk.metadata["index_representation_fingerprint"]
            == REPRESENTATION_FINGERPRINT
        )
        assert (
            mock_chunk.metadata["pr_overlay_representation_fingerprint"]
            == OVERLAY_REPRESENTATION_FINGERPRINT
        )
        assert mock_chunk.metadata["content_state"] == "complete"

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_partial_diff_is_not_parsed_or_embedded_as_source(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im
        partial = SimpleNamespace(
            path="src/service.py",
            content="@@ -1 +1 @@\n-old\n+new",
            change_type="MODIFIED",
            content_state="partial_diff",
        )

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(_request([partial]))

        assert result["status"] == "indexed"
        assert result["chunks_indexed"] == 0
        assert result["partial_files"] == ["src/service.py"]
        im.splitter.split_documents.assert_not_called()
        assert (
            im._file_ops.replace_pr_overlay_generation.call_args.args[0]
            == []
        )

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_partial_diff_fails_closed_for_repository_analysis(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        im = _make_index_manager()
        im.plugin_runtime.repository_analysis_plugins.return_value = ("java",)
        mock_get.return_value = im
        mock_load_snapshots.return_value = (
            (
                SimpleNamespace(
                    plugin_id="java",
                    kind="repository",
                    content="snapshot",
                ),
            ),
            ("java",),
            "sha256:capabilities",
            DESCRIPTOR_FINGERPRINT,
            IMPLEMENTATION_FINGERPRINT,
        )
        mock_build_capabilities.return_value = _capabilities("java")
        partial = SimpleNamespace(
            path="src/Service.java",
            content="@@ -1 +1 @@\n-old\n+new",
            change_type="MODIFIED",
            content_state="partial_diff",
        )
        req = _request([partial])
        req.repository_plugins = ["java"]
        req.plugin_fingerprint = "sha256:capabilities"

        from rag_pipeline.api.routers.pr import index_pr_files

        with pytest.raises(HTTPException) as exc_info:
            index_pr_files(req)

        assert exc_info.value.status_code == 409
        assert "requires complete changed-file source" in exc_info.value.detail
        assert "src/Service.java" in exc_info.value.detail
        im.splitter.split_documents.assert_not_called()
        im.plugin_runtime.start_repository_analysis.assert_not_called()
        im._file_ops._replace_points.assert_not_called()

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_empty_content_clears_previous_generation(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        file_info = MagicMock()
        file_info.content = ""
        file_info.path = "empty.java"
        file_info.change_type = "ADDED"

        req = _request([file_info])

        result = index_pr_files(req)
        assert result["status"] == "indexed"
        assert result["chunks_indexed"] == 0
        assert (
            im._file_ops.replace_pr_overlay_generation.call_args.args[0]
            == []
        )

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_deleted_only_request_clears_previous_generation(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        file_info = MagicMock()
        file_info.content = "class Foo {}"
        file_info.path = "Deleted.java"
        file_info.change_type = "DELETED"

        req = _request([file_info])

        result = index_pr_files(req)
        assert result["status"] == "indexed"
        assert result["chunks_indexed"] == 0
        assert (
            im._file_ops.replace_pr_overlay_generation.call_args.args[0]
            == []
        )

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_replacement_does_not_predelete_existing_pr_points(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        file_info = MagicMock()
        file_info.content = "code"
        file_info.path = "a.java"
        file_info.change_type = "MODIFIED"

        mock_chunk = MagicMock()
        mock_chunk.metadata = {"path": "a.java"}
        im.splitter.split_documents.return_value = [mock_chunk]

        req = _request([file_info])

        result = index_pr_files(req)
        assert result["status"] == "indexed"
        im.qdrant_client.delete.assert_not_called()

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_value_error_raises_400(self, mock_get):
        im = _make_index_manager()
        im._get_project_collection_name.side_effect = ValueError("bad input")
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.pr_number = 42
        req.branch = "feat"
        req.files = []

        with pytest.raises(HTTPException) as exc_info:
            index_pr_files(req)
        assert exc_info.value.status_code == 400

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_internal_error_raises_500(self, mock_get):
        im = _make_index_manager()
        im._ensure_collection_exists.side_effect = RuntimeError("qdrant down")
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.pr_number = 42
        req.branch = "feat"
        req.files = []

        with pytest.raises(HTTPException) as exc_info:
            index_pr_files(req)
        assert exc_info.value.status_code == 500

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_plugins_without_repository_sessions_do_not_require_snapshots(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        im = _make_index_manager()
        im.plugin_runtime.repository_analysis_plugins.return_value = ()
        mock_get.return_value = im
        mock_load_snapshots.return_value = (
            (),
            ("java", "spring"),
            "sha256:capabilities",
            DESCRIPTOR_FINGERPRINT,
            IMPLEMENTATION_FINGERPRINT,
        )
        mock_build_capabilities.return_value = _capabilities("java", "spring")
        req = _request([])
        req.repository_plugins = ["java", "spring"]
        req.plugin_fingerprint = "sha256:capabilities"

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(req)

        assert result["status"] == "indexed"
        assert result["chunks_indexed"] == 0

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_new_non_repository_capability_does_not_require_reindex(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        im = _make_index_manager()
        im.plugin_runtime.repository_analysis_plugins.return_value = ()
        mock_get.return_value = im
        mock_load_snapshots.return_value = (
            (),
            ("java",),
            "sha256:old",
            DESCRIPTOR_FINGERPRINT,
            IMPLEMENTATION_FINGERPRINT,
        )
        mock_build_capabilities.return_value = _capabilities(
            "java",
            "spring",
            fingerprint="sha256:new",
        )
        req = _request([])
        req.repository_plugins = ["java", "spring"]
        req.plugin_fingerprint = "sha256:new"
        req.plugin_detection_evidence = {
            "spring": ["fixture:spring"],
        }

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(req)

        assert result["status"] == "indexed"
        mock_build_capabilities.assert_called_once_with(
            im.plugin_catalog.registry,
            ("java", "spring"),
            "sha256:new",
            (),
            revision="head-commit",
            detection_evidence={
                "java": (
                    "indexed-target:main:sha256:old:java",
                ),
                "spring": ("fixture:spring",),
            },
        )

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_added_files_can_initialize_new_repository_plugins(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        from codecrow_plugins import RepositoryAnalysis

        im = _make_index_manager()
        im.plugin_runtime.repository_analysis_plugins.return_value = (
            "php", "magento",
        )
        handle = MagicMock()
        handle.finish.return_value = (RepositoryAnalysis(), ())
        im.plugin_runtime.start_repository_analysis.return_value = handle
        im._indexer._architecture_nodes.return_value = []
        im._indexer._repository_context_nodes.return_value = []
        mock_get.return_value = im
        mock_load_snapshots.return_value = (
            (),
            ("json",),
            "sha256:target",
            DESCRIPTOR_FINGERPRINT,
            IMPLEMENTATION_FINGERPRINT,
        )
        php = SimpleNamespace(
            path="module/registration.php",
            content="<?php",
            change_type="ADDED",
        )
        module = SimpleNamespace(
            path="module/etc/module.xml",
            content="<config />",
            change_type="ADDED",
        )
        req = _request([php, module])
        req.repository_plugins = ["json", "php", "magento"]
        mock_build_capabilities.return_value = _capabilities(
            "json",
            "php",
            "magento",
            fingerprint=req.plugin_fingerprint,
        )
        req.plugin_detection_evidence = {
            "php": ["extension:module/registration.php"],
            "magento": ["file:module/etc/module.xml"],
        }

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(req)

        assert result["status"] == "indexed"
        handle.ingest.assert_called_once()
        from codecrow_plugins import RepositoryAnalysisMode
        im.plugin_runtime.start_repository_analysis.assert_called_once_with(
            mock_build_capabilities.return_value,
            "head-commit",
            snapshots=(),
            mode=RepositoryAnalysisMode.PR_OVERLAY,
        )

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_modified_evidence_cannot_initialize_new_repository_plugin(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        im = _make_index_manager()
        im.plugin_runtime.repository_analysis_plugins.return_value = ("php",)
        mock_get.return_value = im
        mock_load_snapshots.return_value = (
            (),
            ("json",),
            "sha256:target",
            DESCRIPTOR_FINGERPRINT,
            IMPLEMENTATION_FINGERPRINT,
        )
        mock_build_capabilities.return_value = _capabilities("json", "php")
        php = SimpleNamespace(
            path="src/Existing.php",
            content="<?php",
            change_type="MODIFIED",
        )
        req = _request([php])
        req.repository_plugins = ["json", "php"]
        req.plugin_detection_evidence = {
            "php": ["extension:src/Existing.php"],
        }

        from rag_pipeline.api.routers.pr import index_pr_files

        with pytest.raises(HTTPException) as exc_info:
            index_pr_files(req)

        assert exc_info.value.status_code == 409
        assert "introduced only by added files" in exc_info.value.detail

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_pr_capability_subset_uses_target_branch_identity(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        im = _make_index_manager()
        im.plugin_runtime.repository_analysis_plugins.return_value = ()
        mock_get.return_value = im
        mock_load_snapshots.return_value = (
            (),
            ("bash", "css", "java", "python", "spring"),
            "sha256:target-branch",
            DESCRIPTOR_FINGERPRINT,
            IMPLEMENTATION_FINGERPRINT,
        )
        mock_build_capabilities.return_value = _capabilities(
            "bash",
            "css",
            "java",
            "python",
            "spring",
            fingerprint="sha256:target-branch",
        )
        req = _request([])
        req.repository_plugins = ["java", "spring"]
        req.plugin_fingerprint = "sha256:pr-revision"

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(req)

        assert result["status"] == "indexed"
        mock_build_capabilities.assert_called_once_with(
            im.plugin_catalog.registry,
            ("bash", "css", "java", "python", "spring"),
            "sha256:target-branch",
            (),
            revision="head-commit",
            detection_evidence={
                plugin_id: (
                    "indexed-target:main:sha256:target-branch:"
                    f"{plugin_id}",
                )
                for plugin_id in (
                    "bash",
                    "css",
                    "java",
                    "python",
                    "spring",
                )
            },
        )
        assert result["effective_project_capabilities"][
            "repositoryPlugins"
        ] == ["bash", "css", "java", "python", "spring"]

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_repository_session_plugin_requires_its_snapshot(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        im = _make_index_manager()
        im.plugin_runtime.repository_analysis_plugins.return_value = ("php", "magento")
        mock_get.return_value = im
        mock_load_snapshots.return_value = (
            (),
            ("json", "php", "magento"),
            "sha256:capabilities",
            DESCRIPTOR_FINGERPRINT,
            IMPLEMENTATION_FINGERPRINT,
        )
        mock_build_capabilities.return_value = _capabilities(
            "json",
            "php",
            "magento",
        )
        req = _request([])
        req.repository_plugins = ["json", "php", "magento"]
        req.plugin_fingerprint = "sha256:capabilities"

        from rag_pipeline.api.routers.pr import index_pr_files

        with pytest.raises(HTTPException) as exc_info:
            index_pr_files(req)

        assert exc_info.value.status_code == 409
        assert "target branch 'main'" in exc_info.value.detail
        assert "magento, php" in exc_info.value.detail

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_request_descriptor_identity_mismatch_does_not_block_review(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        im = _make_index_manager()
        mock_get.return_value = im
        mock_load_snapshots.return_value = ((), (), None, None, None)
        req = _request([])
        req.repository_plugins = ["java"]
        req.plugin_detection_evidence = {"java": ("fixture:java",)}
        req.plugin_descriptor_fingerprint = "sha256:" + "9" * 64
        im.plugin_runtime.repository_analysis_plugins.return_value = ()
        mock_build_capabilities.return_value = _capabilities("java")

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(req)

        assert result["status"] == "indexed"
        im._file_ops._replace_points.assert_called_once()

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_stored_plugin_implementation_mismatch_does_not_require_reindex(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        im = _make_index_manager()
        mock_get.return_value = im
        mock_load_snapshots.return_value = (
            (),
            ("java",),
            "sha256:capabilities",
            DESCRIPTOR_FINGERPRINT,
            "sha256:" + "3" * 64,
        )
        req = _request([])
        req.repository_plugins = ["java"]
        im.plugin_runtime.repository_analysis_plugins.return_value = ()
        mock_build_capabilities.return_value = _capabilities("java")

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(req)

        assert result["status"] == "indexed"
        im._file_ops._replace_points.assert_called_once()

    @patch("rag_pipeline.api.routers.pr.build_overlay_capabilities")
    @patch("rag_pipeline.api.routers.pr.load_repository_snapshots")
    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_legacy_index_without_plugin_content_identity_is_accepted(
        self,
        mock_get,
        mock_load_snapshots,
        mock_build_capabilities,
    ):
        im = _make_index_manager()
        mock_get.return_value = im
        mock_load_snapshots.return_value = (
            (),
            ("java",),
            "sha256:capabilities",
            None,
            None,
        )
        req = _request([])
        req.repository_plugins = ["java"]
        im.plugin_runtime.repository_analysis_plugins.return_value = ()
        mock_build_capabilities.return_value = _capabilities("java")

        from rag_pipeline.api.routers.pr import index_pr_files

        result = index_pr_files(req)

        assert result["status"] == "indexed"
        im._file_ops._replace_points.assert_called_once()


# ─────────────────────────────────────────────────────────────
# delete_pr_files
# ─────────────────────────────────────────────────────────────
class TestDeletePRFiles:

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_success(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import delete_pr_files
        result = delete_pr_files("ws", "proj", 42)
        assert result["status"] == "deleted"
        assert result["pr_number"] == 42

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_collection_not_found(self, mock_get):
        im = _make_index_manager()
        im._collection_manager.collection_exists.return_value = False
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import delete_pr_files
        result = delete_pr_files("ws", "proj", 42)
        assert result["status"] == "skipped"

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_error_raises_500(self, mock_get):
        im = _make_index_manager()
        im.qdrant_client.delete.side_effect = RuntimeError("err")
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import delete_pr_files
        with pytest.raises(HTTPException):
            delete_pr_files("ws", "proj", 42)
