from __future__ import annotations

from pathlib import Path

from codecrow_plugins import (
    CandidateClaim,
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectCapabilities,
    ProjectSelector,
    RepositoryFacts,
    ValidationDecision,
)

PLUGINS_ROOT = Path(__file__).resolve().parents[3]


def _capabilities(paths):
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    return catalog, ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=tuple(sorted(paths)),
    ))


def _analyze(paths_to_content):
    catalog, capabilities = _capabilities(paths_to_content)
    runtime = PluginRuntime(catalog)
    handle = runtime.start_repository_analysis(
        capabilities,
        "0123456789abcdef",
    )
    handle.ingest(tuple(
        FileArtifact(path, content)
        for path, content in sorted(paths_to_content.items())
    ))
    analysis, diagnostics = handle.finish()
    assert diagnostics == ()
    return catalog, runtime, capabilities, analysis


def _facts(analysis):
    return {
        fact
        for packet in analysis.packets
        for fact in packet.facts
    }


def test_python_import_and_call_resolve_to_unchanged_policy_path():
    files = {
        "app/export_policy.py": (
            "class ExportPolicy:\n"
            "    @staticmethod\n"
            "    def can_export(user):\n"
            "        return user.role == 'auditor'\n"
        ),
        "app/export_service.py": (
            "from app.export_policy import ExportPolicy\n"
            "\n"
            "def can_export(user):\n"
            "    return ExportPolicy.can_export(user)\n"
        ),
    }

    catalog, runtime, capabilities, analysis = _analyze(files)
    facts = _facts(analysis)

    binding = next(
        fact
        for fact in facts
        if fact.kind == "python-import-binding"
        and fact.source == "app.export_service::ExportPolicy"
    )
    call = next(
        fact
        for fact in facts
        if fact.kind == "python-call-resolution"
        and fact.target == "app.export_policy::can_export"
    )
    assert binding.related_paths == ("app/export_policy.py",)
    assert call.related_paths == ("app/export_policy.py",)

    restored = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        snapshots=analysis.snapshots,
    )
    restored.ingest((FileArtifact(
        "app/export_service.py",
        files["app/export_service.py"].replace(
            "ExportPolicy.can_export(user)",
            "user.active",
        ),
    ),))
    overlaid, diagnostics = restored.finish()

    assert diagnostics == ()
    assert overlaid.snapshots != analysis.snapshots
    assert any(
        fact.kind == "python-import-binding"
        and fact.related_paths == ("app/export_policy.py",)
        for fact in _facts(overlaid)
    )
    assert not any(
        fact.kind == "python-call-resolution"
        and fact.target == "app.export_policy::can_export"
        for fact in _facts(overlaid)
    )


def test_java_same_package_call_resolves_to_policy_path():
    files = {
        "src/main/java/example/RefundPolicy.java": (
            "package example;\n"
            "public final class RefundPolicy {\n"
            "  static boolean canRefund(Order order) { return true; }\n"
            "}\n"
        ),
        "src/main/java/example/RefundService.java": (
            "package example;\n"
            "public final class RefundService {\n"
            "  boolean canRefund(Order order) {\n"
            "    return RefundPolicy.canRefund(order);\n"
            "  }\n"
            "}\n"
        ),
    }

    _, runtime, capabilities, analysis = _analyze(files)
    facts = _facts(analysis)

    binding = next(
        fact
        for fact in facts
        if fact.kind == "java-import-binding"
        and fact.source == "example::RefundPolicy"
    )
    call = next(
        fact
        for fact in facts
        if fact.kind == "java-call-resolution"
        and fact.target == "example::canRefund"
    )
    assert binding.related_paths == (
        "src/main/java/example/RefundPolicy.java",
    )
    assert call.related_paths == (
        "src/main/java/example/RefundPolicy.java",
    )

    restored = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        snapshots=analysis.snapshots,
    )
    restored.ingest((FileArtifact(
        "src/main/java/example/RefundService.java",
        (
            "package example;\n"
            "public final class RefundService {\n"
            "  boolean canRefund(Order order) {\n"
            "    return order.paid();\n"
            "  }\n"
            "}\n"
        ),
    ),))
    overlaid, diagnostics = restored.finish()

    assert diagnostics == ()
    assert not any(
        fact.kind == "java-call-resolution"
        and fact.target == "example::canRefund"
        for fact in _facts(overlaid)
    )


def test_line_only_movement_does_not_publish_removed_relation():
    files = {
        "app/policy.py": "def allowed(user):\n    return user.active\n",
        "app/service.py": (
            "from app.policy import allowed\n"
            "\n"
            "def can_run(user):\n"
            "    return allowed(user)\n"
        ),
    }
    _, runtime, capabilities, analysis = _analyze(files)
    restored = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        snapshots=analysis.snapshots,
    )
    restored.ingest((FileArtifact(
        "app/service.py",
        "\n" + files["app/service.py"],
    ),))
    overlaid, diagnostics = restored.finish()

    assert diagnostics == ()
    assert any(
        fact.kind == "python-call-resolution"
        and fact.target == "app.policy::allowed"
        for fact in _facts(overlaid)
    )


def test_typescript_import_and_call_resolve_to_exported_policy():
    files = {
        "src/downloadPolicy.ts": (
            "export interface User { id: string; active: boolean }\n"
            "export interface Report { ownerId: string; ready: boolean }\n"
            "export const mayDownload = "
            "(report: Report, user: User): boolean => "
            "report.ownerId === user.id && user.active;\n"
        ),
        "src/reportService.ts": (
            "import { mayDownload, type Report, type User } "
            "from './downloadPolicy';\n"
            "export const canDownload = "
            "(report: Report, user: User): boolean => "
            "mayDownload(report, user);\n"
        ),
    }

    catalog, runtime, capabilities, analysis = _analyze(files)
    facts = _facts(analysis)

    binding = next(
        fact
        for fact in facts
        if fact.kind == "typescript-import-binding"
        and fact.source == "src/reportService::mayDownload"
    )
    call = next(
        fact
        for fact in facts
        if fact.kind == "typescript-call-resolution"
        and fact.target == "src/downloadPolicy::mayDownload"
    )
    assert binding.related_paths == ("src/downloadPolicy.ts",)
    assert call.related_paths == ("src/downloadPolicy.ts",)
    assert runtime.repository_analysis_plugins(capabilities) == ("typescript",)
    assert catalog.implementation("typescript") is not None


def test_tsx_only_repository_resolves_multiline_relative_imports_with_jsx_syntax():
    files = {
        "src/aggregateFields.tsx": "export const isBaseVisualize = (value: unknown) => Boolean(value);\n",
        "src/index.tsx": (
            "import {\n  isBaseVisualize,\n} from './aggregateFields';\n"
            "export const Panel = () => <div>{String(isBaseVisualize({}))}</div>;\n"
        ),
    }
    _, runtime, capabilities, analysis = _analyze(files)
    facts = _facts(analysis)
    binding = next(f for f in facts if f.kind == 'typescript-import-binding'
                   and f.source == 'src::isBaseVisualize')
    assert binding.related_paths == ('src/aggregateFields.tsx',)
    assert any(f.kind == 'typescript-call-resolution' and f.target == 'src/aggregateFields::isBaseVisualize'
               for f in facts)
    # TSX retains its existing syntax owner; the repository graph is shared
    # with TypeScript without routing JSX through the TypeScript-only grammar.
    assert capabilities.file_plugins['src/index.tsx'] == ('tsx',)
    assert runtime.syntax_contribution('src/index.tsx', capabilities) == (None, ())
    assert runtime.repository_analysis_plugins(capabilities) == ('typescript',)


def test_typescript_and_tsx_share_relative_import_resolution():
    _, _, _, analysis = _analyze({
        'src/policy.ts': 'export const allowed = () => true;\n',
        'src/Panel.tsx': "import {allowed} from './policy';\nexport const Panel = () => <div>{String(allowed())}</div>;\n",
        'src/page.ts': "import {Panel} from './Panel';\nexport const page = () => Panel();\n",
    })
    facts = _facts(analysis)
    assert any(f.kind == 'typescript-call-resolution' and f.related_paths == ('src/policy.ts',) for f in facts)
    assert any(f.kind == 'typescript-call-resolution' and f.related_paths == ('src/Panel.tsx',) for f in facts)


def test_typescript_validator_rejects_coarse_or_contradicted_relation_claims():
    files = {
        "src/downloadPolicy.ts": (
            "export const mayDownload = (): boolean => true;\n"
        ),
        "src/reportService.ts": (
            "import { mayDownload } from './downloadPolicy';\n"
            "export const canDownload = (): boolean => mayDownload();\n"
        ),
    }
    _, runtime, capabilities, analysis = _analyze(files)
    evidence = tuple(sorted(_facts(analysis)))

    coarse = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/reportService.ts",
            line=1,
            message="The TypeScript file is incorrect.",
            evidence=evidence,
            claim_kind="typescript-file",
        ),
        capabilities,
    )
    contradicted = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/reportService.ts",
            line=1,
            message="reportService does not import mayDownload.",
            evidence=evidence,
            claim_kind="typescript-import-binding",
        ),
        capabilities,
    )

    assert coarse[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert contradicted[0].decision is ValidationDecision.REJECT


def test_python_package_initializer_relative_import_resolves_inside_package():
    _, _, _, analysis = _analyze({
        "app/__init__.py": "from .policy import Policy\nPolicy.check()\n",
        "app/policy.py": "class Policy:\n    def check(self): pass\n",
    })
    assert any(
        fact.kind == "python-call-resolution"
        and fact.path == "app/__init__.py"
        and fact.related_paths == ("app/policy.py",)
        for fact in _facts(analysis)
    )


def test_python_colocated_stub_does_not_replace_runtime_source_target():
    _, _, _, analysis = _analyze({
        "app/policy.py": "class Policy: pass\n",
        "app/policy.pyi": "class Policy: ...\n",
        "app/use.py": "from app.policy import Policy\n",
    })
    assert any(
        fact.kind == "python-module-resolution"
        and fact.path == "app/use.py"
        and fact.related_paths == ("app/policy.py",)
        for fact in _facts(analysis)
    )


def test_python_package_initializer_wins_over_same_name_module():
    _, _, _, analysis = _analyze({
        "app/policy.py": "class Policy: pass\n",
        "app/policy/__init__.py": "class Policy: pass\n",
        "app/use.py": "from app.policy import Policy\n",
    })
    assert any(
        fact.kind == "python-module-resolution"
        and fact.path == "app/use.py"
        and fact.related_paths == ("app/policy/__init__.py",)
        for fact in _facts(analysis)
    )


def test_python_newer_syntax_preserves_repository_import_and_call_edges():
    files = {
        "app/policy.py": "def allow(user):\n    return bool(user)\n",
        "app/service.py": (
            "from app.policy import allow as permitted\n"
            "type Pair[T] = tuple[T, T]\n"
            "def run[T](user: T) -> bool:\n"
            "    return permitted(user)\n"
        ),
        "app/labels.py": (
            "import app.policy as policy\n"
            "def label(user):\n"
            '    text = f"{user["name"]}"\n'
            "    return policy.allow(text)\n"
        ),
    }
    _, _, _, analysis = _analyze(files)
    facts = _facts(analysis)
    assert any(fact.kind == "python-import-binding"
               and fact.source == "app.service::permitted" and fact.line == 1
               and fact.related_paths == ("app/policy.py",) for fact in facts)
    assert any(fact.kind == "python-call-resolution" and fact.path == "app/service.py"
               and fact.line == 4 and fact.target == "app.policy::allow" for fact in facts)
    assert any(fact.kind == "python-call-resolution" and fact.path == "app/labels.py"
               and fact.line == 4 and fact.target == "app.policy::allow" for fact in facts)


def test_python_newer_syntax_keeps_local_import_call_and_declaration_facts():
    source = (
        "from app.policy import allow\n"
        "type Pair[T] = tuple[T, T]\n"
        "class Service[T](Base):\n"
        "    @staticmethod\n"
        "    def run(user: T) -> bool:\n"
        "        return allow(user)\n"
    )
    catalog, capabilities = _capabilities(["app/service.py"])
    facts, diagnostics = PluginRuntime(catalog).graph_facts(
        FileArtifact("app/service.py", source), capabilities,
    )
    assert diagnostics == ()
    assert any(fact.kind == "python-import" and fact.target == "app.policy.allow" for fact in facts)
    assert any(fact.kind == "python-type" and fact.target == "app.service.Service" for fact in facts)
    assert any(fact.kind == "python-inheritance" and fact.target == "Base" for fact in facts)
    assert any(fact.kind == "python-callable" and fact.source == "app.service.Service"
               and fact.target == "run" and fact.line == 5 for fact in facts)
    assert any(fact.kind == "python-call" and fact.target == "allow" and fact.line == 6 for fact in facts)
    assert any(fact.kind == "python-decorator" and fact.target == "staticmethod" for fact in facts)


def test_python_malformed_syntax_stays_observably_unavailable():
    catalog, capabilities = _capabilities(["app/broken.py"])
    runtime = PluginRuntime(catalog)
    artifact = FileArtifact("app/broken.py", "def broken(:\n    return 1\n")
    facts, diagnostics = runtime.graph_facts(artifact, capabilities)
    assert not facts
    assert any(d.code == "python-parse-failed" for d in diagnostics)
    handle = runtime.start_repository_analysis(capabilities, "revision")
    handle.ingest((artifact,))
    _, diagnostics = handle.finish()
    assert any(d.code == "plugin-repository-file-skipped" and d.path == artifact.path for d in diagnostics)


def test_python_syntax_fallback_preserves_regular_fact_semantics():
    source = (
        "from .policy import allow as allowed\n"
        "import os.path as paths\n"
        "answer = 42\n"
        "@decorate(flag=True)\n"
        "class Service(Generic[int]):\n"
        "    @staticmethod\n"
        "    async def run(value):\n"
        "        return allowed(paths.join(value))\n"
    )
    path = "app/service.py"
    catalog, capabilities = _capabilities([path])
    runtime = PluginRuntime(catalog)
    native, diagnostics = runtime.graph_facts(FileArtifact(path, source), capabilities)
    assert not diagnostics
    # Select grammar fallback without changing line coordinates or ordinary facts.
    newer = source.replace("class Service(", "class Service[T](")
    fallback, diagnostics = runtime.graph_facts(FileArtifact(path, newer), capabilities)
    assert not diagnostics
    assert fallback == native
    _, _, _, native_analysis = _analyze({path: source})
    _, _, _, fallback_analysis = _analyze({path: newer})
    assert fallback_analysis.snapshots == native_analysis.snapshots
