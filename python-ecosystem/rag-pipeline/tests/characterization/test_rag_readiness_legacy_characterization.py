"""P0-02 characterization of Python RAG partial-readiness behavior."""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rag_pipeline.core.index_manager.indexer import FileOperations
from rag_pipeline.models.config import IndexStats


pytestmark = pytest.mark.legacy_defect

FIXTURE = Path(__file__).parent / "fixtures" / "v1" / "rag_readiness.json"
SOURCE_REVISION = "89287e1fce55dc9bffeca2b92ce660d8791ae6ac"


def _digest(document):
    canonical = json.dumps(
        {key: value for key, value in document.items() if key != "digest"},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_rag_readiness_golden_is_source_bound_and_digest_verified():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert set(golden) == {
        "schemaVersion", "sourceRevision", "scenario", "observedResult",
        "defect", "pre", "post", "published", "digest",
    }
    assert golden["schemaVersion"] == 1
    assert golden["sourceRevision"] == SOURCE_REVISION
    assert golden["digest"] == _digest(golden)


@patch("rag_pipeline.api.routers.pr._get_index_manager")
def test_legacy_defect_partial_pr_upsert_is_published_as_indexed(mock_get):
    manager = MagicMock()
    manager._get_project_collection_name.return_value = "rag_ws__proj"
    chunk = MagicMock()
    chunk.metadata = {"path": "src/Foo.java"}
    manager.splitter.split_documents.return_value = [chunk, chunk, chunk]
    manager._point_ops.embed_and_create_points.return_value = [MagicMock(), MagicMock(), MagicMock()]
    manager._point_ops.upsert_points.return_value = (1, 2)
    mock_get.return_value = manager

    file_info = MagicMock(
        content="public class Foo {}",
        path="src/Foo.java",
        change_type="MODIFIED",
    )
    request = MagicMock(
        workspace="ws",
        project="proj",
        pr_number=42,
        branch="feature",
        files=[file_info],
    )

    from rag_pipeline.api.routers.pr import index_pr_files

    result = index_pr_files(request)

    assert result == {
        "status": "indexed",
        "pr_number": 42,
        "files_processed": 1,
        "chunks_indexed": 1,
        "chunks_failed": 2,
    }


def test_legacy_defect_incremental_update_deletes_before_replacement_loads():
    events = []
    client = MagicMock()
    client.delete.side_effect = lambda **_kwargs: events.append("delete-old")
    loader = MagicMock()

    def no_replacement(**_kwargs):
        events.append("load-replacement")
        return []

    loader.load_specific_files.side_effect = no_replacement
    stats_manager = MagicMock()
    stats_manager.get_project_stats.return_value = MagicMock(spec=IndexStats)
    operations = FileOperations(
        client=client,
        point_ops=MagicMock(),
        collection_manager=MagicMock(),
        stats_manager=stats_manager,
        splitter=MagicMock(),
        loader=loader,
    )

    operations.update_files(
        file_paths=["src/Foo.java"],
        repo_base="/offline-repo",
        workspace="ws",
        project="proj",
        branch="main",
        commit="head-a",
        collection_name="rag_ws__proj",
    )

    assert events == ["delete-old", "load-replacement"]
    operations.point_ops.process_and_upsert_chunks.assert_not_called()
