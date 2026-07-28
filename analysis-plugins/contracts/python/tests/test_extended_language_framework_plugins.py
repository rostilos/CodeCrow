from __future__ import annotations

from pathlib import Path

from codecrow_plugins import (
    FileArtifact, PluginCatalog, PluginRuntime, ProjectSelector, RepositoryFacts,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
JAVA_CONTROLLER = "java-ecosystem/services/pipeline-agent/src/main/java/org/rostilos/codecrow/pipelineagent/generic/controller/ProviderWebhookController.java"
PLATFORM_MCP_SERVER = "java-ecosystem/mcp-servers/platform-mcp/src/main/java/org/rostilos/codecrow/platformmcp/PlatformMcpServer.java"
FASTAPI_APP = "python-ecosystem/rag-pipeline/src/rag_pipeline/api/api.py"
FASTAPI_ROUTER = "python-ecosystem/rag-pipeline/src/rag_pipeline/api/routers/pr.py"
JAVASCRIPT_CONFIG = "frontend/eslint.config.js"


def _source(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _runtime_for_current_sources():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    paths = tuple(sorted((JAVA_CONTROLLER, FASTAPI_APP, FASTAPI_ROUTER, JAVASCRIPT_CONFIG)))
    marker_contents = {
        JAVA_CONTROLLER: _source(JAVA_CONTROLLER),
        FASTAPI_APP: _source(FASTAPI_APP),
    }
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef", paths=paths, marker_contents=marker_contents,
    ))
    return PluginRuntime(catalog), capabilities


def test_current_project_selects_deep_language_and_framework_plugins():
    _, capabilities = _runtime_for_current_sources()

    assert capabilities.repository_plugins == (
        "java", "javascript", "python", "fastapi", "spring",
    )
    assert any(
        item.startswith("content-pattern:**/*.java:")
        for item in capabilities.detection_evidence["spring"]
    )
    assert any(
        item.startswith("content-pattern:**/*.py:")
        for item in capabilities.detection_evidence["fastapi"]
    )


def test_current_java_source_produces_java_and_spring_architecture_context():
    runtime, capabilities = _runtime_for_current_sources()
    facts, diagnostics = runtime.graph_facts(
        FileArtifact(JAVA_CONTROLLER, _source(JAVA_CONTROLLER)), capabilities,
    )
    triples = {(fact.kind, fact.relation, fact.target) for fact in facts}

    assert diagnostics == ()
    assert ("java-type", "declares", "org.rostilos.codecrow.pipelineagent.generic.controller.ProviderWebhookController") in triples
    assert ("spring-route", "handles", "POST /api/webhooks/{provider}/{authToken}") in triples
    assert ("spring-injection", "depends-on", "WebhookProjectResolver") in triples
    assert any(
        fact.kind == "spring-component"
        and ("stereotype", "RestController") in fact.attributes
        for fact in facts
    )


def test_current_large_java_source_keeps_tree_alive_for_complete_traversal():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    source = _source(PLATFORM_MCP_SERVER)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=tuple(sorted((JAVA_CONTROLLER, PLATFORM_MCP_SERVER))),
        marker_contents={JAVA_CONTROLLER: _source(JAVA_CONTROLLER)},
    ))

    assert capabilities.repository_plugins == ("java", "spring")
    facts, diagnostics = PluginRuntime(catalog).graph_facts(
        FileArtifact(PLATFORM_MCP_SERVER, source),
        capabilities,
    )

    assert diagnostics == ()
    assert any(
        fact.kind == "java-type"
        and fact.target == "org.rostilos.codecrow.platformmcp.PlatformMcpServer"
        for fact in facts
    )
    assert any(
        fact.kind == "java-callable"
        and fact.target == "getToolSpecifications"
        for fact in facts
    )


def test_current_python_sources_produce_python_and_fastapi_architecture_context():
    runtime, capabilities = _runtime_for_current_sources()
    app_facts, app_diagnostics = runtime.graph_facts(
        FileArtifact(FASTAPI_APP, _source(FASTAPI_APP)), capabilities,
    )
    route_facts, route_diagnostics = runtime.graph_facts(
        FileArtifact(FASTAPI_ROUTER, _source(FASTAPI_ROUTER)), capabilities,
    )

    assert app_diagnostics == route_diagnostics == ()
    assert any(fact.kind == "python-import" and fact.target == "fastapi.FastAPI" for fact in app_facts)
    assert any(fact.kind == "fastapi-application" and fact.target == "app" for fact in app_facts)
    assert any(fact.kind == "fastapi-middleware" and fact.target == "ServiceSecretMiddleware" for fact in app_facts)
    assert any(fact.kind == "fastapi-router" and fact.relation == "includes" and fact.target == "pr_router" for fact in app_facts)
    assert any(fact.kind == "fastapi-route" and fact.target == "POST /index/pr-files" for fact in route_facts)


def test_current_javascript_source_produces_module_and_call_context():
    runtime, capabilities = _runtime_for_current_sources()
    facts, diagnostics = runtime.graph_facts(
        FileArtifact(JAVASCRIPT_CONFIG, _source(JAVASCRIPT_CONFIG)), capabilities,
    )

    assert diagnostics == ()
    assert any(fact.kind == "javascript-import" for fact in facts)
    assert any(fact.kind == "javascript-export" for fact in facts)
    assert any(fact.kind == "javascript-call" for fact in facts)


def test_go_plugin_is_deep_but_current_project_has_no_go_source():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    path = "cmd/server/main.go"
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef", paths=(path,),
    ))
    source = '''package main
import "net/http"
type Server struct{}
func (server *Server) Serve() { http.ListenAndServe(":8080", nil) }
'''
    facts, diagnostics = PluginRuntime(catalog).graph_facts(
        FileArtifact(path, source), capabilities,
    )
    kinds = {fact.kind for fact in facts}

    assert diagnostics == ()
    assert {"go-package", "go-import", "go-type", "go-callable", "go-call"} <= kinds
    current_go_sources = tuple(
        path for path in REPOSITORY_ROOT.rglob("*.go")
        if not {"target", ".venv", ".git"}.intersection(path.parts)
    )
    assert current_go_sources == ()


def test_framework_detection_does_not_classify_generic_language_projects():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    java_path = "src/main/java/example/App.java"
    python_path = "src/app.py"
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=tuple(sorted((java_path, python_path))),
        marker_contents={
            java_path: "package example; public class App {}",
            python_path: "def run(): return 1",
        },
    ))

    assert capabilities.repository_plugins == ("java", "python")
