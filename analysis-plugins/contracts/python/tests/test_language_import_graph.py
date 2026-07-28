from __future__ import annotations

from pathlib import Path

from codecrow_plugins import (
    CandidateClaim,
    FileArtifact,
    PluginCatalog,
    PluginRuntime,
    ProjectCapabilities,
    ProjectSelector,
    RepositoryAnalysisMode,
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
        mode=RepositoryAnalysisMode.PR_OVERLAY,
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
    removed_call = next(
        fact
        for fact in _facts(overlaid)
        if fact.kind == "python-pr-removed-relation"
        and dict(fact.attributes)["originalKind"] == "python-call-resolution"
    )
    assert removed_call.target == "app.export_policy::can_export"
    assert removed_call.related_paths == ("app/export_policy.py",)


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
        mode=RepositoryAnalysisMode.PR_OVERLAY,
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
    removed = {
        dict(fact.attributes)["originalKind"]: fact
        for fact in _facts(overlaid)
        if fact.kind == "java-pr-removed-relation"
    }
    assert removed["java-call-resolution"].target == "example::canRefund"
    assert removed["java-call-resolution"].related_paths == (
        "src/main/java/example/RefundPolicy.java",
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
        mode=RepositoryAnalysisMode.PR_OVERLAY,
    )
    restored.ingest((FileArtifact(
        "app/service.py",
        "\n" + files["app/service.py"],
    ),))
    overlaid, diagnostics = restored.finish()

    assert diagnostics == ()
    assert not any(
        fact.kind == "python-pr-removed-relation"
        for fact in _facts(overlaid)
    )


def test_persistent_incremental_does_not_store_pr_transition_facts():
    files = {
        "app/policy.py": "def allowed(user):\n    return user.active\n",
        "app/service.py": (
            "from app.policy import allowed\n"
            "def can_run(user):\n"
            "    return allowed(user)\n"
        ),
    }
    _, runtime, capabilities, analysis = _analyze(files)
    restored = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        snapshots=analysis.snapshots,
        mode=RepositoryAnalysisMode.PERSISTENT_INCREMENTAL,
    )
    restored.ingest((FileArtifact(
        "app/service.py",
        "def can_run(user):\n    return user.active\n",
    ),))

    updated, diagnostics = restored.finish()

    assert diagnostics == ()
    assert not any(
        fact.kind.endswith("-pr-removed-relation")
        or packet.kind.endswith("-import-graph-delta")
        for packet in updated.packets
        for fact in packet.facts
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
