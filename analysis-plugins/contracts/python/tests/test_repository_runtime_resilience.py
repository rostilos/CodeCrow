from types import SimpleNamespace

from codecrow_plugins import (
    FileArtifact,
    PluginDiagnostic,
    PluginOutcome,
    RepositoryAnalysis,
)
from codecrow_plugins.runtime import RepositoryAnalysisHandle


class _FileIsolatingSession:
    def __init__(self):
        self.ingested = []

    def ingest(self, artifacts):
        artifact = artifacts[0]
        if artifact.path == "bad.xml":
            raise ValueError("invalid project file")
        self.ingested.append(artifact.path)

    def finish(self, _dependencies):
        return PluginOutcome.handled(RepositoryAnalysis(
            diagnostics=(PluginDiagnostic(
                code="project-warning",
                message="recoverable repository diagnostic",
                plugin_id="test-plugin",
                path="warning.xml",
                recoverable=True,
            ),),
        ))


def test_repository_runtime_quarantines_ingest_failure_and_keeps_session():
    session = _FileIsolatingSession()
    runtime = SimpleNamespace(
        MAX_REPOSITORY_SYMBOLS=10,
        MAX_ARCHITECTURE_PACKETS=10,
    )
    handle = RepositoryAnalysisHandle(
        runtime,
        [("test-plugin", session)],
        [],
    )

    handle.ingest((
        FileArtifact("bad.xml", "<invalid>"),
        FileArtifact("good.xml", "<valid />"),
    ))
    _analysis, diagnostics = handle.finish()

    assert session.ingested == ["good.xml"]
    assert [
        (diagnostic.code, diagnostic.path, diagnostic.recoverable)
        for diagnostic in diagnostics
    ] == [
        ("plugin-repository-file-skipped", "bad.xml", True),
        ("project-warning", "warning.xml", True),
    ]
