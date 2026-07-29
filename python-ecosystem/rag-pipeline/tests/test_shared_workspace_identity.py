"""Deployment contract for cross-container repository workspace ownership."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_pipeline_agent_and_rag_images_share_workspace_numeric_identity():
    pipeline_dockerfiles = (
        "java-ecosystem/services/pipeline-agent/Dockerfile",
        "java-ecosystem/services/pipeline-agent/Dockerfile.observable",
    )
    rag_dockerfiles = (
        "python-ecosystem/rag-pipeline/Dockerfile",
        "python-ecosystem/rag-pipeline/Dockerfile.observable",
    )

    for relative_path in pipeline_dockerfiles:
        content = _text(relative_path)
        assert re.search(r"\baddgroup\s+-g\s+1001\b", content)
        assert re.search(r"\badduser\s+-u\s+1001\b", content)

    for relative_path in rag_dockerfiles:
        content = _text(relative_path)
        assert re.search(r"\bgroupadd\s+--gid\s+1001\b", content)
        assert re.search(r"\buseradd\s+--uid\s+1001\b", content)


def test_shared_temp_root_retains_sticky_bit_protection():
    for relative_path in (
        "deployment/docker-compose.yml",
        "deployment/docker-compose.prod.yml",
    ):
        content = _text(relative_path)
        assert "source_code_tmp:/tmp" in content
        assert re.search(r"chmod\s+-R\s+1777\s+/tmp", content)
