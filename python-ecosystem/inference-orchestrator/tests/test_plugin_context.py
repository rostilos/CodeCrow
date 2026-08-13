from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from model.dtos import ReviewRequestDto
from model.enrichment import FileContentDto, PrEnrichmentDataDto
from model.multi_stage import FileGroup, ReviewFile, ReviewPlan
from model.output_schemas import CodeReviewIssue
from model.plugins import ProjectCapabilitiesDto
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review import plugin_context
from utils.diff_processor import DiffProcessor, HunkDisposition


@pytest.mark.asyncio
async def test_plugin_diagnostic_capture_is_review_scoped():
    async def capture(code: str):
        records = []
        diagnostic = SimpleNamespace(
            plugin_id="neutral-language",
            code=code,
            message="synthetic diagnostic",
        )
        with plugin_context.capture_plugin_diagnostics(records.append):
            await asyncio.sleep(0)
            plugin_context._log_plugin_diagnostics(
                "review validation",
                (diagnostic,),
            )
        return records

    left, right = await asyncio.gather(
        capture("left-exception"),
        capture("right-warning"),
    )

    assert [item["code"] for item in left] == ["left-exception"]
    assert [item["code"] for item in right] == ["right-warning"]


def _request() -> ReviewRequestDto:
    contents = [
        FileContentDto(
            path="composer.json",
            content='{"require":{"magento/framework":"*"}}',
            sizeBytes=44,
        ),
        FileContentDto(
            path="app/etc/config.php",
            content="<?php return ['modules' => ['Acme_Checkout' => 1]];",
            sizeBytes=54,
        ),
        FileContentDto(path="bin/magento", content="#!/usr/bin/env php", sizeBytes=18),
        FileContentDto(
            path="app/code/Acme/Checkout/etc/module.xml",
            content='<config><module name="Acme_Checkout" /></config>',
            sizeBytes=49,
        ),
        FileContentDto(
            path="app/code/Acme/Checkout/etc/di.xml",
            content=(
                '<config><preference for="Acme\\Api\\CartInterface" '
                'type="Acme\\Model\\Cart" /></config>'
            ),
            sizeBytes=100,
        ),
        FileContentDto(
            path="app/code/Acme/Checkout/Model/Cart.php",
            content="<?php namespace Acme\\Model; class Cart {}",
            sizeBytes=45,
        ),
    ]
    return ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="workspace",
        projectVcsRepoSlug="repository",
        projectWorkspace="workspace",
        projectNamespace="project",
        aiProvider="OPENAI",
        aiModel="provided-model",
        aiApiKey="secret",
        currentCommitHash="0123456789abcdef",
        changedFiles=[
            "app/code/Acme/Checkout/Model/Cart.php",
            "app/code/Acme/Checkout/etc/di.xml",
            "app/code/Acme/Checkout/etc/module.xml",
            "app/etc/config.php",
            "composer.json",
        ],
        enrichmentData=PrEnrichmentDataDto(fileContents=contents),
    )


def _request_with_capabilities() -> ReviewRequestDto:
    request = _request()
    _, _, selector = plugin_context._plugin_host()
    from codecrow_plugins import RepositoryFacts

    paths = tuple(sorted(path.lstrip("/") for path in request.changedFiles))
    capabilities = selector.select(RepositoryFacts(
        revision=request.currentCommitHash,
        paths=paths,
        marker_contents={
            "composer.json": (
                '{"require":{"magento/framework":"*"}}'
            ),
        },
    ))
    request.projectCapabilities = ProjectCapabilitiesDto(
        repositoryPlugins=list(capabilities.repository_plugins),
        filePlugins={
            path: list(plugin_ids)
            for path, plugin_ids in capabilities.file_plugins.items()
        },
        detectionEvidence={
            plugin_id: list(evidence)
            for plugin_id, evidence
            in capabilities.detection_evidence.items()
        },
        unavailableCapabilities=list(
            capabilities.unavailable_capabilities
        ),
        fingerprint=capabilities.fingerprint,
        descriptorFingerprint=capabilities.descriptor_fingerprint,
    )
    return request


def _capabilities_dto(
    repository_plugins,
    file_plugins,
) -> ProjectCapabilitiesDto:
    catalog, _, selector = plugin_context._plugin_host()
    from codecrow_plugins import ProjectCapabilities

    repository_plugins = tuple(repository_plugins)
    normalized_files = {
        path: tuple(plugin_ids)
        for path, plugin_ids in file_plugins.items()
    }
    evidence = {
        plugin_id: (f"fixture:{plugin_id}",)
        for plugin_id in repository_plugins
    }
    fingerprint = selector._fingerprint(
        "0123456789abcdef",
        repository_plugins,
        normalized_files,
        evidence,
    )
    capabilities = ProjectCapabilities(
        repository_plugins=repository_plugins,
        file_plugins=normalized_files,
        detection_evidence=evidence,
        unavailable_capabilities=(),
        fingerprint=fingerprint,
        descriptor_fingerprint=(
            catalog.registry.fingerprint_for(repository_plugins)
        ),
    )
    return ProjectCapabilitiesDto(
        repositoryPlugins=list(capabilities.repository_plugins),
        filePlugins={
            path: list(plugin_ids)
            for path, plugin_ids in capabilities.file_plugins.items()
        },
        detectionEvidence={
            plugin_id: list(values)
            for plugin_id, values
            in capabilities.detection_evidence.items()
        },
        unavailableCapabilities=[],
        fingerprint=capabilities.fingerprint,
        descriptorFingerprint=capabilities.descriptor_fingerprint,
    )


def test_cross_runtime_plugin_projection_is_validated_before_review_context():
    plugin_context._plugin_host.cache_clear()
    request = _request_with_capabilities()

    resolved = plugin_context.resolve_project_capabilities(request)

    assert resolved.repository_plugins == (
        "json",
        "php",
    )


def test_rag_effective_projection_activates_complete_repository_plugin():
    plugin_context._plugin_host.cache_clear()
    request = _request()
    request.currentCommitHash = "0123456789abcdef"
    request.changedFiles = [
        "backend/Invoice.java",
        "web/invoice.ts",
        "worker/invoice.py",
    ]
    request.projectCapabilities = _capabilities_dto(
        ("java", "python", "typescript"),
        {
            "backend/Invoice.java": ("java",),
            "web/invoice.ts": ("typescript",),
            "worker/invoice.py": ("python",),
        },
    )
    catalog, _, selector = plugin_context._plugin_host()
    effective_ids = tuple(
        descriptor.id
        for descriptor in catalog.registry.resolve(
            ("data-contracts", "java", "python", "typescript")
        )
    )
    effective = selector.project(
        revision=request.currentCommitHash,
        repository_plugins=effective_ids,
        file_plugins={
            "backend/Invoice.java": ("java",),
            "web/invoice.ts": ("typescript",),
            "worker/invoice.py": ("python",),
        },
        detection_evidence={
            plugin_id: (
                f"indexed-target:main:sha256:target:{plugin_id}",
            )
            for plugin_id in effective_ids
        },
    )
    payload = {
        "repositoryPlugins": list(effective.repository_plugins),
        "filePlugins": {
            path: list(plugin_ids)
            for path, plugin_ids in effective.file_plugins.items()
        },
        "detectionEvidence": {
            plugin_id: list(values)
            for plugin_id, values in effective.detection_evidence.items()
        },
        "unavailableCapabilities": [],
        "fingerprint": effective.fingerprint,
        "descriptorFingerprint": effective.descriptor_fingerprint,
        "implementationFingerprint": (
            catalog.implementation_fingerprint(effective_ids)
        ),
    }

    resolved = plugin_context.apply_effective_project_capabilities(
        request,
        payload,
    )

    assert resolved.repository_plugins == effective_ids
    assert "data-contracts" in request.projectCapabilities.repositoryPlugins
    context = plugin_context.review_plugin_context(
        request,
        request.changedFiles,
    )
    assert "navigation evidence only" in context


def test_rag_effective_projection_accepts_implementation_provenance_drift():
    plugin_context._plugin_host.cache_clear()
    request = _request_with_capabilities()
    capabilities = request.projectCapabilities
    payload = capabilities.model_dump()
    payload["implementationFingerprint"] = "sha256:" + "0" * 64

    resolved = plugin_context.apply_effective_project_capabilities(
        request,
        payload,
    )

    assert resolved.repository_plugins == tuple(capabilities.repositoryPlugins)


@pytest.mark.parametrize("field", ["descriptorFingerprint", "fingerprint"])
def test_cross_runtime_plugin_fingerprint_mismatch_is_provenance_only(field):
    plugin_context._plugin_host.cache_clear()
    request = _request_with_capabilities()
    expected_plugins = tuple(request.projectCapabilities.repositoryPlugins)
    setattr(
        request.projectCapabilities,
        field,
        "sha256:" + "0" * 64,
    )

    resolved = plugin_context.resolve_project_capabilities(request)

    assert resolved.repository_plugins == expected_plugins
    assert resolved.fingerprint != request.projectCapabilities.fingerprint or (
        field == "descriptorFingerprint"
    )


def test_magento_does_not_inject_static_prompt_rules():
    plugin_context._plugin_host.cache_clear()

    context = plugin_context.review_plugin_context(_request(), _request().changedFiles)

    assert "effective DI preference" not in context
    assert len(context) <= 6000


def test_magento_file_policy_removes_generated_hunks_but_keeps_architecture():
    plugin_context._plugin_host.cache_clear()
    request = _request()
    processed = DiffProcessor().process(
        """diff --git a/generated/code/Acme/Proxy.php b/generated/code/Acme/Proxy.php
new file mode 100644
--- /dev/null
+++ b/generated/code/Acme/Proxy.php
@@ -0,0 +1 @@
+<?php class Proxy {}
diff --git a/app/code/Acme/Checkout/etc/di.xml b/app/code/Acme/Checkout/etc/di.xml
--- a/app/code/Acme/Checkout/etc/di.xml
+++ b/app/code/Acme/Checkout/etc/di.xml
@@ -1 +1 @@
-<config />
+<config><preference for="A" type="B" /></config>
"""
    )

    result = plugin_context.apply_plugin_file_policy(request, processed)

    by_path = {item.path: item for item in result.files}
    generated = by_path["generated/code/Acme/Proxy.php"]
    architecture = by_path["app/code/Acme/Checkout/etc/di.xml"]
    assert generated.is_skipped is True
    assert generated.plugin_disposition == "generated"
    assert {hunk.disposition for hunk in generated.hunks} == {
        HunkDisposition.GENERATED
    }
    assert architecture.is_skipped is False
    assert architecture.plugin_disposition == "architecture-only"
    assert result.total_files == 1
    assert result.skipped_files == 1


def test_file_policy_failure_keeps_file_reviewable(monkeypatch):
    runtime = MagicMock()
    runtime.file_disposition.side_effect = RuntimeError("broken-policy")
    monkeypatch.setattr(
        plugin_context,
        "_plugin_host",
        lambda: (MagicMock(), runtime, MagicMock()),
    )
    monkeypatch.setattr(
        plugin_context,
        "resolve_project_capabilities",
        lambda _request: MagicMock(),
    )
    processed = DiffProcessor().process(
        """diff --git a/example.php b/example.php
--- a/example.php
+++ b/example.php
@@ -1 +1 @@
-old
+new
"""
    )

    records = []
    with plugin_context.capture_plugin_diagnostics(records.append):
        result = plugin_context.apply_plugin_file_policy(_request(), processed)

    assert result.files[0].is_skipped is False
    assert result.files[0].plugin_disposition is None
    assert [item["code"] for item in records] == [
        "plugin-file-policy-exception"
    ]


def test_file_policy_setup_failure_keeps_diff_reviewable(monkeypatch):
    monkeypatch.setattr(
        plugin_context,
        "_plugin_host",
        MagicMock(side_effect=RuntimeError("broken-host")),
    )
    processed = DiffProcessor().process(
        "diff --git a/example.py b/example.py\n"
        "--- a/example.py\n+++ b/example.py\n@@ -1 +1 @@\n-old\n+new\n"
    )

    records = []
    with plugin_context.capture_plugin_diagnostics(records.append):
        result = plugin_context.apply_plugin_file_policy(_request(), processed)

    assert result is processed
    assert result.files[0].is_skipped is False
    assert [item["code"] for item in records] == [
        "plugin-file-policy-setup-exception"
    ]


def test_review_context_omits_incomplete_optional_plugin_contribution(
    monkeypatch,
):
    from codecrow_plugins import PluginDiagnostic, ReviewContribution

    runtime = MagicMock()
    runtime.review_contribution.return_value = (
        ReviewContribution(),
        (
            PluginDiagnostic(
                code="plugin-review-exception",
                message="ValueError: broken contribution",
                plugin_id="python",
            ),
        ),
    )
    monkeypatch.setattr(
        plugin_context,
        "_plugin_host",
        lambda: (MagicMock(), runtime, MagicMock()),
    )
    monkeypatch.setattr(
        plugin_context,
        "resolve_project_capabilities",
        lambda _request: MagicMock(),
    )

    records = []
    with plugin_context.capture_plugin_diagnostics(records.append):
        context = plugin_context.review_plugin_context(_request(), ["example.py"])

    assert context == ""
    assert [item["code"] for item in records] == ["plugin-review-exception"]


def test_review_context_runtime_exception_fails_open(monkeypatch):
    runtime = MagicMock()
    runtime.review_contribution.side_effect = RuntimeError("broken-contribution")
    monkeypatch.setattr(
        plugin_context,
        "_plugin_host",
        lambda: (MagicMock(), runtime, MagicMock()),
    )
    monkeypatch.setattr(
        plugin_context,
        "resolve_project_capabilities",
        lambda _request: MagicMock(),
    )

    records = []
    with plugin_context.capture_plugin_diagnostics(records.append):
        context = plugin_context.review_plugin_context(_request(), ["example.py"])

    assert context == ""
    assert [item["code"] for item in records] == ["plugin-review-exception"]


def test_review_plan_preserves_host_plan_when_plugin_contribution_is_incomplete(
    monkeypatch,
):
    from codecrow_plugins import PluginDiagnostic, ReviewContribution

    runtime = MagicMock()
    runtime.review_contribution.return_value = (
        ReviewContribution(),
        (
            PluginDiagnostic(
                code="plugin-review-exception",
                message="ValueError: broken contribution",
                plugin_id="python",
            ),
        ),
    )
    monkeypatch.setattr(
        plugin_context,
        "_plugin_host",
        lambda: (MagicMock(), runtime, MagicMock()),
    )
    monkeypatch.setattr(
        plugin_context,
        "resolve_project_capabilities",
        lambda _request: MagicMock(),
    )
    plan = ReviewPlan(
        analysis_summary="host-owned",
        file_groups=[FileGroup(
            group_id="host",
            priority="MEDIUM",
            rationale="mandatory coverage",
            files=[ReviewFile(path="example.py")],
        )],
    )

    records = []
    with plugin_context.capture_plugin_diagnostics(records.append):
        result = plugin_context.apply_plugin_plan_constraints(plan, _request())

    assert result is plan
    assert [item["code"] for item in records] == ["plugin-review-exception"]


def test_review_plan_runtime_exception_preserves_host_plan(monkeypatch):
    runtime = MagicMock()
    runtime.review_contribution.side_effect = RuntimeError("broken-contribution")
    monkeypatch.setattr(
        plugin_context,
        "_plugin_host",
        lambda: (MagicMock(), runtime, MagicMock()),
    )
    monkeypatch.setattr(
        plugin_context,
        "resolve_project_capabilities",
        lambda _request: MagicMock(),
    )
    plan = ReviewPlan(
        analysis_summary="host-owned",
        file_groups=[FileGroup(
            group_id="host",
            priority="MEDIUM",
            rationale="mandatory coverage",
            files=[ReviewFile(path="example.py")],
        )],
    )

    records = []
    with plugin_context.capture_plugin_diagnostics(records.append):
        result = plugin_context.apply_plugin_plan_constraints(plan, _request())

    assert result is plan
    assert [item["code"] for item in records] == [
        "plugin-review-planning-exception"
    ]


def test_magento_does_not_group_by_path_shape_without_graph_evidence():
    plugin_context._plugin_host.cache_clear()
    request = _request()
    plan = ReviewPlan(
        analysis_summary="test",
        file_groups=[FileGroup(
            group_id="generic",
            priority="MEDIUM",
            rationale="generic",
            files=[ReviewFile(path=path) for path in request.changedFiles],
        )],
    )

    result = plugin_context.apply_plugin_plan_constraints(plan, request)

    assert result.file_groups[0].group_id == "generic"


def test_magento_repository_graph_groups_override_generic_stage_zero_grouping():
    plugin_context._plugin_host.cache_clear()
    request = _request()
    plan = ReviewPlan(
        analysis_summary="test",
        file_groups=[FileGroup(
            group_id="generic",
            priority="MEDIUM",
            rationale="generic",
            files=[ReviewFile(path=path) for path in request.changedFiles],
        )],
    )

    result = plugin_context.apply_plugin_plan_constraints(
        plan,
        request,
        repository_group_paths=(
            (
                "app/code/Acme/Checkout/etc/di.xml",
                "app/code/Acme/Checkout/Model/Cart.php",
            ),
            (
                "app/code/Acme/Checkout/Model/Cart.php",
                "app/code/Acme/Checkout/etc/module.xml",
            ),
        ),
    )

    assert result.file_groups[0].group_id == "PLUGIN_EVIDENCE_001"
    assert [item.path for item in result.file_groups[0].files] == [
        "app/code/Acme/Checkout/Model/Cart.php",
        "app/code/Acme/Checkout/etc/di.xml",
        "app/code/Acme/Checkout/etc/module.xml",
    ]
    assert [
        item.path
        for group in result.file_groups
        for item in group.files
    ].count("app/code/Acme/Checkout/Model/Cart.php") == 1


def test_magento_gate_rejects_absence_claim_contradicted_by_exact_di_fact():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/etc/di.xml",
        line=1,
        title="Missing Magento preference",
        reason="Magento has no preference registered for CartInterface.",
        suggestedFixDescription="Register the preference.",
        codeSnippet='<preference for="Acme\\Api\\CartInterface" type="Acme\\Model\\Cart" />',
    )

    ledger = CandidateEvidenceLedger()
    ledger.register(
        issue,
        stage="stage_1",
        source_key="plugin-gate:0",
        review_unit_ids=("sha256:unit",),
        prompt_hunk_ids=("sha256:hunk",),
        generation_prompt="plugin validation prompt",
    )
    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        candidate_ledger=ledger,
    )

    assert result == []
    ledger.assert_terminal()
    assert ledger.summary()["rejectionCounts"] == {
        "plugin_evidence:magento-absence-contradicted": 1
    }


def test_plugin_contradiction_closes_open_history_instead_of_silently_dropping_it():
    plugin_context._plugin_host.cache_clear()
    request = _request()
    request.previousCodeAnalysisIssues = [{"id": "history-1", "status": "open"}]
    issue = CodeReviewIssue(
        id="history-1",
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/etc/di.xml",
        line=1,
        title="Missing Magento preference",
        reason="Magento has no preference registered for CartInterface.",
        suggestedFixDescription="Register the preference.",
        codeSnippet='<preference for="Acme\\Api\\CartInterface" type="Acme\\Model\\Cart" />',
    )

    result = plugin_context.apply_plugin_validation_gate([issue], request)

    assert result == [issue]
    assert issue.isResolved is True
    assert "magento-absence-contradicted" in issue.resolutionReason


def test_magento_gate_withholds_uncited_owned_relationship_claim():
    plugin_context._plugin_host.cache_clear()
    request = _request()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/etc/di.xml",
        line=1,
        title="Magento preference targets an invalid implementation",
        reason="The Magento preference resolves CartInterface to the wrong implementation.",
        suggestedFixDescription="Change the preference.",
        codeSnippet='<preference for="Acme\\Api\\CartInterface" type="Acme\\Model\\Cart" />',
    )

    assert plugin_context.apply_plugin_validation_gate([issue], request) == []


def test_magento_gate_withholds_structural_presence_citation():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/etc/di.xml",
        line=1,
        title="Magento preference targets invalid implementation",
        reason="The Magento preference resolves CartInterface to the wrong implementation.",
        suggestedFixDescription="Change the preference target.",
        codeSnippet='<preference for="Acme\\Api\\CartInterface" type="Acme\\Model\\Cart" />',
        evidenceRefs=["RAG-preference"],
    )
    evidence = {
        "RAG-preference": [{
            "kind": "magento-di-effective-preference",
            "source": "Acme\\Api\\CartInterface",
            "relation": "resolves-to",
            "target": "Acme\\Model\\Cart",
            "path": "app/code/Acme/Checkout/etc/di.xml",
            "line": 1,
            "attributes": {"area": "global"},
            "related_paths": ["app/code/Acme/Checkout/Model/Cart.php"],
        }],
    }

    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id=evidence,
        deterministic_retrieval_states=["complete"],
    )

    assert result == []


def test_magento_gate_withholds_claim_whose_citation_has_wrong_relation_kind():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/etc/di.xml",
        line=1,
        title="Magento preference targets invalid implementation",
        reason="The Magento preference resolves CartInterface to the wrong implementation.",
        suggestedFixDescription="Change the preference target.",
        codeSnippet='<preference for="Acme\\Api\\CartInterface" type="Acme\\Model\\Cart" />',
        evidenceRefs=["RAG-route"],
    )
    evidence = {
        "RAG-route": [{
            "kind": "magento-effective-route",
            "source": "checkout",
            "relation": "handled-by",
            "target": "Acme_Checkout",
            "path": "app/code/Acme/Checkout/etc/frontend/routes.xml",
            "line": 1,
            "attributes": {"area": "frontend"},
            "related_paths": [],
        }],
    }

    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id=evidence,
        deterministic_retrieval_states=["complete"],
    )

    assert result == []


def test_magento_gate_withholds_claim_with_invented_citation():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/etc/di.xml",
        line=1,
        title="Missing Magento preference",
        reason="Magento has no preference registered for CartInterface.",
        suggestedFixDescription="Register the preference.",
        codeSnippet="<config />",
        evidenceRefs=["RAG-invented"],
    )

    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id={"RAG-real": []},
        deterministic_retrieval_states=["complete"],
    )

    assert result == []


def test_generic_claim_with_invented_citation_is_withheld():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/Model/Cart.php",
        line=1,
        title="Possible null dereference",
        reason="The changed method dereferences a nullable local value.",
        suggestedFixDescription="Guard the nullable value.",
        codeSnippet="$value->execute();",
        evidenceRefs=["RAG-invented"],
    )

    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id={"RAG-real": []},
        deterministic_retrieval_states=["complete"],
    )

    assert result == []


def test_generic_claim_with_available_semantic_citation_is_kept():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/Model/Cart.php",
        line=1,
        title="Possible null dereference",
        reason=(
            "The changed method dereferences a nullable local value and the "
            "cited caller passes null."
        ),
        suggestedFixDescription="Guard the nullable value.",
        codeSnippet="$value->execute();",
        evidenceRefs=["RAG-semantic"],
    )

    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id={"RAG-semantic": ()},
        deterministic_retrieval_states=["complete"],
    )

    assert result == [issue]


def test_optional_plugin_validation_exception_keeps_candidate(monkeypatch):
    runtime = MagicMock()
    runtime.graph_facts.return_value = ((), ())
    runtime.start_repository_analysis.side_effect = RuntimeError(
        "broken repository analyzer"
    )
    runtime.validate_with_diagnostics.side_effect = RuntimeError(
        "broken validator"
    )
    monkeypatch.setattr(
        plugin_context,
        "_plugin_host",
        lambda: (MagicMock(), runtime, MagicMock()),
    )
    monkeypatch.setattr(
        plugin_context,
        "resolve_project_capabilities",
        lambda _request: MagicMock(),
    )
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/Model/Cart.php",
        line=1,
        title="Current source dereferences null",
        reason="The changed method dereferences a nullable local value.",
        suggestedFixDescription="Guard the nullable value.",
        codeSnippet="$value->execute();",
    )

    records = []
    with plugin_context.capture_plugin_diagnostics(records.append):
        result = plugin_context.apply_plugin_validation_gate(
            [issue],
            _request(),
        )

    assert result == [issue]
    assert {item["code"] for item in records} == {
        "plugin-repository-validation-exception",
        "plugin-candidate-validation-exception",
    }


def test_optional_plugin_validation_setup_exception_keeps_candidate(monkeypatch):
    monkeypatch.setattr(
        plugin_context,
        "_plugin_host",
        MagicMock(side_effect=RuntimeError("broken-host")),
    )
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="src/app.py",
        line=1,
        title="Current source dereferences null",
        reason="The changed method dereferences a nullable value.",
        suggestedFixDescription="Guard the nullable value.",
        codeSnippet="value.run()",
    )

    records = []
    with plugin_context.capture_plugin_diagnostics(records.append):
        result = plugin_context.apply_plugin_validation_gate([issue], _request())

    assert result == [issue]
    assert [item["code"] for item in records] == [
        "plugin-candidate-validation-setup-exception"
    ]


def test_candidate_cannot_use_review_wide_evidence_from_another_prompt():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/Model/Cart.php",
        line=1,
        title="Possible null dereference",
        reason="The cited caller passes null into the changed dereference.",
        suggestedFixDescription="Guard the nullable value.",
        codeSnippet="$value->execute();",
        evidenceRefs=["RAG-other-batch"],
    )
    ledger = CandidateEvidenceLedger()
    ledger.register(
        issue,
        stage="stage_1",
        source_key="batch-1:0",
        review_unit_ids=("sha256:unit",),
        prompt_hunk_ids=("sha256:hunk",),
        generation_prompt="plugin validation prompt",
        visible_evidence_by_id={"RAG-this-batch": ()},
    )

    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id={"RAG-other-batch": ()},
        deterministic_retrieval_states=["complete"],
        candidate_ledger=ledger,
    )

    assert result == []
    ledger.assert_terminal()
    assert ledger.summary()["rejectionCounts"] == {
        "plugin_evidence:unavailable_evidence_ref": 1
    }


def test_hyva_gate_rejects_untyped_explicit_absence_contradiction():
    plugin_context._plugin_host.cache_clear()
    path = (
        "app/design/frontend/Acme/custom/Acme_Sales/"
        "templates/orders/init.phtml"
    )
    request = _request().model_copy(update={
        "changedFiles": [path],
        "enrichmentData": PrEnrichmentDataDto(fileContents=[]),
        "projectCapabilities": _capabilities_dto(
            ["php", "magento", "hyva"],
            {path: ["php"]},
        ),
    })
    base = {
        "severity": "HIGH",
        "category": "BUG_RISK",
        "file": path,
        "line": 8,
        "suggestedFixDescription": "Correct the endpoint integration.",
        "codeSnippet": "fetch('/rest/V1/acme/orders/list', {method: 'POST'})",
        "evidenceRefs": ["RAG-hyva-route"],
    }
    contradicted = CodeReviewIssue(
        **base,
        title="Hyva endpoint is missing",
        reason="POST /V1/acme/orders/list/ endpoint does not exist.",
    )
    semantic = CodeReviewIssue(
        **base,
        title="Hyva payload is incompatible",
        reason=(
            "POST /V1/acme/orders/list/ returns an incompatible payload."
        ),
    )
    evidence = {
        "RAG-hyva-route": [{
            "kind": "hyva-template-webapi-reference",
            "source": path,
            "relation": "calls-webapi-route",
            "target": "POST /V1/acme/orders/list/",
            "path": path,
            "line": 8,
            "attributes": {},
            "related_paths": [
                "app/code/Acme/Sales/etc/webapi.xml",
            ],
        }],
    }

    assert plugin_context.apply_plugin_validation_gate(
        [contradicted],
        request,
        exact_evidence_by_id=evidence,
        deterministic_retrieval_states=["complete"],
    ) == []
    assert plugin_context.apply_plugin_validation_gate(
        [semantic],
        request,
        exact_evidence_by_id=evidence,
        deterministic_retrieval_states=["complete"],
    ) == [semantic]


def test_magento_gate_withholds_disabled_observer_presence_without_rejecting():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Audit/etc/frontend/events.xml",
        line=1,
        title="Magento frontend observer is disabled",
        reason=(
            "The Magento observer Acme\\Checkout\\Observer\\Audit is disabled "
            "for checkout_submit_all_after in the frontend area."
        ),
        suggestedFixDescription=(
            "Account for the missing frontend side effect."
        ),
        codeSnippet='<observer name="acme_audit" disabled="true" />',
        evidenceRefs=["RAG-disabled-observer"],
    )
    evidence = {
        "RAG-disabled-observer": [{
            "kind": "magento-effective-observer",
            "source": "checkout_submit_all_after",
            "relation": "disables-observer",
            "target": "Acme\\Checkout\\Observer\\Audit",
            "path": "app/code/Acme/Audit/etc/frontend/events.xml",
            "line": 1,
            "attributes": {"area": "frontend"},
            "related_paths": [],
        }],
    }

    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id=evidence,
        deterministic_retrieval_states=["complete"],
    )

    assert result == []


def test_magento_gate_withholds_webapi_acl_structural_presence():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/etc/webapi.xml",
        line=1,
        title="Magento webapi requires cart ACL",
        reason=(
            "The Magento webapi POST:/V1/acme/cart requires ACL resource "
            "Acme_Checkout::cart."
        ),
        suggestedFixDescription="Correct authorization for the route.",
        codeSnippet='<resource ref="Acme_Checkout::cart" />',
        evidenceRefs=["RAG-webapi-acl"],
    )
    evidence = {
        "RAG-webapi-acl": [{
            "kind": "magento-webapi-acl",
            "source": "POST:/V1/acme/cart",
            "relation": "requires-resource",
            "target": "Acme_Checkout::cart",
            "path": "app/code/Acme/Checkout/etc/webapi.xml",
            "line": 1,
            "attributes": {},
            "related_paths": ["app/code/Acme/Checkout/etc/acl.xml"],
        }],
    }

    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id=evidence,
        deterministic_retrieval_states=["complete"],
    )

    assert result == []


def test_magento_gate_does_not_use_frontend_route_as_webapi_evidence():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/etc/webapi.xml",
        line=1,
        title="Magento webapi route exposes checkout",
        reason="The Magento webapi route POST:/V1/acme/cart exposes checkout.",
        suggestedFixDescription="Correct the Web API declaration.",
        codeSnippet='<route url="/V1/acme/cart" method="POST">',
        evidenceRefs=["RAG-frontend-route"],
    )
    evidence = {
        "RAG-frontend-route": [{
            "kind": "magento-effective-route",
            "source": "frontend:acme",
            "relation": "resolves-controller",
            "target": "Acme\\Checkout\\Controller\\Index\\Index",
            "path": "app/code/Acme/Checkout/etc/frontend/routes.xml",
            "line": 1,
            "attributes": {},
            "related_paths": [],
        }],
    }

    result = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id=evidence,
        deterministic_retrieval_states=["complete"],
    )

    assert result == []


def test_generic_php_issue_is_not_reclassified_by_the_host():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/Model/Cart.php",
        line=1,
        title="Return value is ignored",
        reason="The result of this method is discarded.",
        suggestedFixDescription="Use the returned value.",
        codeSnippet="save();",
    )

    assert plugin_context.apply_plugin_validation_gate([issue], _request()) == [issue]


def test_php_repository_presence_cannot_approve_semantic_defect():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/Model/Cart.php",
        line=1,
        title="Parent behavior affects cart persistence",
        reason=(
            "Acme\\Checkout\\Model\\Cart extends "
            "Acme\\Checkout\\Model\\BaseCart, so the parent contract affects "
            "this change."
        ),
        suggestedFixDescription="Preserve the parent contract.",
        codeSnippet="class Cart extends BaseCart",
        claimKind="php-inheritance",
        evidenceRefs=["RAG-php-parent"],
    )
    evidence = {
        "RAG-php-parent": [{
            "kind": "php-inheritance",
            "source": "Acme\\Checkout\\Model\\Cart",
            "relation": "extends",
            "target": "Acme\\Checkout\\Model\\BaseCart",
            "path": "app/code/Acme/Checkout/Model/Cart.php",
            "line": 1,
            "attributes": {
                "sourceKind": "class",
                "targetKind": "class",
            },
            "related_paths": [
                "app/code/Acme/Checkout/Model/BaseCart.php",
            ],
        }],
    }

    supported = plugin_context.apply_plugin_validation_gate(
        [issue],
        _request(),
        exact_evidence_by_id=evidence,
        deterministic_retrieval_states=["complete"],
    )
    mismatched = plugin_context.apply_plugin_validation_gate(
        [issue.model_copy(update={
            "claimKind": "php-constructor-dependency",
        })],
        _request(),
        exact_evidence_by_id=evidence,
        deterministic_retrieval_states=["complete"],
    )

    assert supported == []
    assert mismatched == []


def test_typed_php_file_presence_cannot_approve_semantic_defect():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/Model/Cart.php",
        line=1,
        title="PHP relationship is inconsistent",
        reason="The declared type conflicts with the consumer contract.",
        suggestedFixDescription="Correct the declared type.",
        codeSnippet="class Cart {}",
        claimKind="php-file",
        evidenceRefs=["RAG-php"],
    )
    evidence = {
        "RAG-php": [{
            "kind": "php-type",
            "source": "Acme\\Model\\Cart",
            "relation": "declares",
            "target": "Cart",
            "path": "app/code/Acme/Checkout/Model/Cart.php",
            "line": 1,
            "attributes": {},
            "related_paths": [],
        }],
    }

    assert plugin_context.apply_plugin_validation_gate(
        [issue], _request(), exact_evidence_by_id=evidence
    ) == []


def test_javascript_claims_require_exact_relationship_proof():
    plugin_context._plugin_host.cache_clear()
    path = "src/Product.jsx"
    request = _request().model_copy(update={
        "changedFiles": [path, "src/Card.jsx"],
        "enrichmentData": PrEnrichmentDataDto(fileContents=[
            FileContentDto(
                path=path,
                content=(
                    'import Card from "./Card";\n'
                    "export function Product({ name }) {\n"
                    "  return <Card title={name} />;\n"
                    "}"
                ),
                sizeBytes=112,
            ),
            FileContentDto(
                path="src/Card.jsx",
                content=(
                    "export default function Card({ title }) {\n"
                    "  return <div>{title}</div>;\n"
                    "}"
                ),
                sizeBytes=81,
            ),
        ]),
        "projectCapabilities": _capabilities_dto(
            ["javascript"],
            {
                path: ["javascript"],
                "src/Card.jsx": ["javascript"],
            },
        ),
    })
    presence_evidence = {
        "RAG-js-presence": [{
            "kind": "javascript-jsx-prop-contract",
            "source": "src/Product.jsx::Product::Card",
            "relation": "passes-declared-prop",
            "target": "src/Card.jsx::Card::title",
            "path": path,
            "line": 3,
            "attributes": {},
            "related_paths": ["src/Card.jsx"],
        }],
    }
    defect_evidence = {
        "RAG-js-defect": [{
            "kind": "javascript-jsx-required-prop-missing",
            "source": "src/Product.jsx::Product::Card",
            "relation": "omits-required-prop",
            "target": "src/Card.jsx::Card::title",
            "path": path,
            "line": 3,
            "attributes": {},
            "related_paths": ["src/Card.jsx"],
        }],
    }
    base = {
        "severity": "HIGH",
        "category": "BUG_RISK",
        "file": path,
        "line": 3,
        "suggestedFixDescription": "Correct the Card title contract.",
        "codeSnippet": "return <Card title={name} />;",
    }
    presence_only = CodeReviewIssue(
        **base,
        title="Card title type is incompatible",
        reason="Product passes Card title with an incompatible value.",
        claimKind="javascript-jsx-prop-contract",
        evidenceRefs=["RAG-js-presence"],
    )
    supported = CodeReviewIssue(
        **base,
        title="Required Card title is missing",
        reason="Product omits the required Card title prop.",
        claimKind="javascript-jsx-required-prop-missing",
        evidenceRefs=["RAG-js-defect"],
    )
    contradicted = presence_only.model_copy(update={
        "title": "Card title is missing",
        "reason": "Product does not pass the required Card title prop.",
    })
    unrelated = presence_only.model_copy(update={
        "title": "Currency prop is incompatible",
        "reason": "Checkout passes an incompatible currency prop.",
    })
    coarse = presence_only.model_copy(update={
        "claimKind": "javascript-file",
    })

    assert plugin_context.apply_plugin_validation_gate(
        [presence_only],
        request,
        exact_evidence_by_id=presence_evidence,
    ) == []
    assert plugin_context.apply_plugin_validation_gate(
        [supported],
        request,
        exact_evidence_by_id=defect_evidence,
    ) == [supported]
    assert plugin_context.apply_plugin_validation_gate(
        [contradicted],
        request,
        exact_evidence_by_id=presence_evidence,
    ) == []
    assert plugin_context.apply_plugin_validation_gate(
        [unrelated],
        request,
        exact_evidence_by_id=presence_evidence,
    ) == []
    assert plugin_context.apply_plugin_validation_gate(
        [coarse],
        request,
        exact_evidence_by_id=presence_evidence,
    ) == []


def test_typed_claim_without_citation_is_withheld():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/Model/Cart.php",
        line=1,
        title="PHP relationship is inconsistent",
        reason="The declared type conflicts with the consumer contract.",
        suggestedFixDescription="Correct the declared type.",
        codeSnippet="class Cart {}",
        claimKind="php-file",
    )

    assert plugin_context.apply_plugin_validation_gate([issue], _request()) == []


def test_typed_claim_unhandled_by_active_plugins_is_withheld():
    plugin_context._plugin_host.cache_clear()
    issue = CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="app/code/Acme/Checkout/Model/Cart.php",
        line=1,
        title="Unknown framework relationship",
        reason="An unsupported relationship is claimed.",
        suggestedFixDescription="Correct the relationship.",
        codeSnippet="class Cart {}",
        claimKind="unknown-component",
        evidenceRefs=["RAG-php"],
    )
    evidence = {
        "RAG-php": [{
            "kind": "php-type",
            "source": "Acme\\Model\\Cart",
            "relation": "declares",
            "target": "Cart",
            "path": "app/code/Acme/Checkout/Model/Cart.php",
            "line": 1,
            "attributes": {},
            "related_paths": [],
        }],
    }

    assert plugin_context.apply_plugin_validation_gate(
        [issue], _request(), exact_evidence_by_id=evidence
    ) == []


def test_typed_spring_claim_reaches_framework_validator_with_normal_category():
    plugin_context._plugin_host.cache_clear()
    path = "src/main/java/com/example/CheckoutController.java"
    request = _request().model_copy(update={
        "changedFiles": [path],
        "enrichmentData": PrEnrichmentDataDto(fileContents=[]),
        "projectCapabilities": _capabilities_dto(
            ["java", "spring"],
            {path: ["java"]},
        ),
    })
    issue = CodeReviewIssue(
        severity="HIGH",
        category="ARCHITECTURE",
        file=path,
        line=1,
        title="Spring component wiring is inconsistent",
        reason="The controller dependency resolves to an incompatible component.",
        suggestedFixDescription="Correct the component dependency.",
        codeSnippet="class CheckoutController {}",
        claimKind="spring-component",
        evidenceRefs=["RAG-spring"],
    )
    evidence = {
        "RAG-spring": [{
            "kind": "spring-component",
            "source": "com.example.CheckoutController",
            "relation": "declares-component",
            "target": "CheckoutController",
            "path": path,
            "line": 1,
            "attributes": {},
            "related_paths": [],
        }],
    }

    assert plugin_context.apply_plugin_validation_gate(
        [issue], request, exact_evidence_by_id=evidence
    ) == [issue]


def test_review_context_groups_repeated_evidence_and_preserves_line_boundaries():
    plugin_context._plugin_host.cache_clear()
    paths = [
        f"src/main/java/com/example/component/Service{index:03d}.java"
        for index in range(100)
    ]
    request = _request().model_copy(update={
        "changedFiles": paths,
        "projectCapabilities": _capabilities_dto(
            ["java", "spring"],
            {path: ["java"] for path in paths},
        ),
    })

    context = plugin_context.review_plugin_context(request, paths)

    assert len(context) <= plugin_context.PLUGIN_CONTEXT_CHAR_BUDGET
    assert context.count(
        "exact Java declarations, imports, inheritance, and call facts"
    ) == 1
    assert context.count(
        "exact Spring component, route, dependency-injection, bean, "
        "repository, and configuration facts"
    ) == 1
    assert "set claimKind to its exact evidence class" in context
    assert "Service000.java" in context
    assert (
        "60 review path(s) have no plugin evidence request; do not treat this "
        "omission as proof"
    ) in context
    assert all(
        line.startswith((
            "Deterministic ",
                "Exact ",
                "Evidence ",
                "For a ",
                "- ",
            "  - ",
            "[",
        ))
        for line in context.splitlines()
    )

    planning_context = plugin_context.review_plugin_context(
        request,
        paths,
        include_evidence_targets=False,
    )
    assert "exact Java declarations, imports, inheritance, and call facts" in planning_context
    assert "exact Spring component, route, dependency-injection" in planning_context
    assert "Evidence targets:" not in planning_context
    assert "Service000.java" not in planning_context


def test_stage_prompt_targets_only_prompt_visible_plugin_facts():
    plugin_context._plugin_host.cache_clear()
    visible_path = "src/main/java/com/example/VisibleController.java"
    hidden_path = "src/main/java/com/example/OrdinaryService.java"
    paths = [visible_path, hidden_path]
    request = _request().model_copy(update={
        "changedFiles": paths,
        "projectCapabilities": _capabilities_dto(
            ["java", "spring"],
            {path: ["java"] for path in paths},
        ),
    })
    visible_evidence = {
        "RAG-spring": [{
            "kind": "spring-component",
            "source": "com.example.VisibleController",
            "relation": "declares-component",
            "target": "VisibleController",
            "path": visible_path,
            "line": 1,
            "attributes": {},
            "related_paths": [],
        }],
    }

    context = plugin_context.review_plugin_context(
        request,
        paths,
        visible_evidence_by_id=visible_evidence,
    )

    assert visible_path in context
    assert hidden_path not in context
    assert "plugin evidence target(s) omitted because no matching exact fact" in context
    assert "do not create typed framework claims from plugin rules alone" in context


def test_stage_prompt_suppresses_all_typed_targets_without_visible_facts():
    plugin_context._plugin_host.cache_clear()
    path = "src/main/java/com/example/OrdinaryService.java"
    request = _request().model_copy(update={
        "changedFiles": [path],
        "projectCapabilities": _capabilities_dto(
            ["java", "spring"],
            {path: ["java"]},
        ),
    })

    context = plugin_context.review_plugin_context(
        request,
        [path],
        visible_evidence_by_id={},
    )

    assert path not in context
    assert "Exact evidence required before matching claims:" not in context
    assert "do not create typed framework claims from plugin rules alone" in context
