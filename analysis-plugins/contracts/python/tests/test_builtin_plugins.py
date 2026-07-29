from __future__ import annotations

from pathlib import Path

import pytest

from codecrow_plugins import (
    CandidateClaim,
    FileArtifact,
    FileDisposition,
    GraphFact,
    PluginCatalog,
    PluginRuntime,
    ProjectCapabilities,
    ProjectSelector,
    RepositoryFacts,
    ValidationDecision,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]


def test_python_host_accepts_an_empty_plugin_root(tmp_path: Path):
    (tmp_path / "languages").mkdir()
    (tmp_path / "frameworks").mkdir()
    (tmp_path / "domains").mkdir()

    catalog = PluginCatalog.discover(tmp_path)

    assert catalog.registry.ordered_ids == ()
    assert catalog.implementations == {}


def test_empty_plugin_runtime_preserves_neutral_fallback(tmp_path: Path):
    (tmp_path / "languages").mkdir()
    (tmp_path / "frameworks").mkdir()
    (tmp_path / "domains").mkdir()
    catalog = PluginCatalog.discover(tmp_path)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=("src/service.unknown",),
    ))

    assert capabilities.repository_plugins == ()
    assert runtime.file_disposition(
        "src/service.unknown",
        capabilities,
    ) is FileDisposition.FULL
    facts, graph_diagnostics = runtime.graph_facts(
        FileArtifact(path="src/service.unknown", content="opaque source"),
        capabilities,
    )
    assert facts == ()
    assert graph_diagnostics == ()

    handle = runtime.start_repository_analysis(
        capabilities,
        revision="0123456789abcdef",
    )
    assert handle.active is False
    analysis, repository_diagnostics = handle.finish()
    assert analysis.symbols == ()
    assert analysis.packets == ()
    assert analysis.snapshots == ()
    assert repository_diagnostics == ()

    contribution, review_diagnostics = runtime.review_contribution(
        ("src/service.unknown",),
        capabilities,
    )
    assert contribution.rules == ()
    assert contribution.evidence_requests == ()
    assert contribution.group_paths == ()
    assert review_diagnostics == ()
    syntax, syntax_diagnostics = runtime.syntax_contribution(
        "src/service.unknown",
        capabilities,
    )
    assert syntax is None
    assert syntax_diagnostics == ()


def test_selected_language_plugins_own_their_syntax_declarations():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    expected = {
        "src/main.py": (
            "python",
            "tree_sitter_python",
            "language",
            True,
            True,
        ),
        "src/App.java": (
            "java",
            "tree_sitter_java",
            "language",
            True,
            False,
        ),
        "src/app.js": (
            "javascript",
            "tree_sitter_javascript",
            "language",
            True,
            True,
        ),
        "src/app.ts": (
            "typescript",
            "tree_sitter_typescript",
            "language_typescript",
            False,
            True,
        ),
        "cmd/main.go": (
            "go",
            "tree_sitter_go",
            "language",
            True,
            True,
        ),
        "src/App.php": (
            "php",
            "tree_sitter_php",
            "language_php",
            True,
            True,
        ),
    }
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=tuple(sorted(expected)),
    ))

    for path, declaration in expected.items():
        syntax, diagnostics = runtime.syntax_contribution(path, capabilities)

        assert diagnostics == ()
        assert syntax is not None
        assert (
            syntax.language_id,
            syntax.grammar_module,
            syntax.grammar_factory,
            syntax.builtin_tags,
            syntax.rich_traversal_safe,
        ) == declaration
        assert syntax.plugin_id in capabilities.file_plugins[path]
        assert syntax.query_resource == "python/resources/rag-chunks.scm"


def test_unassigned_file_uses_neutral_syntax_fallback_in_polyglot_repository():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=(
            ".htaccess",
            "app/code/Acme/Checkout/Controller/Save.php",
            "app/code/Acme/Checkout/view/frontend/web/js/checkout.js",
            "composer.json",
        ),
    ))

    assert {"javascript", "php"} <= set(capabilities.repository_plugins)
    assert ".htaccess" not in capabilities.file_plugins

    syntax, diagnostics = runtime.syntax_contribution(".htaccess", capabilities)

    assert syntax is None
    assert diagnostics == ()


def test_explicit_multiple_syntax_owners_still_fail_closed():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectCapabilities(
        repository_plugins=("javascript", "php"),
        file_plugins={".htaccess": ("javascript", "php")},
        detection_evidence={},
        fingerprint="sha256:" + ("0" * 64),
    )

    with pytest.raises(
        RuntimeError,
        match=r"conflicting syntax contributions for \.htaccess: javascript, php",
    ):
        runtime.syntax_contribution(".htaccess", capabilities)


def _facts() -> RepositoryFacts:
    return RepositoryFacts(
        revision="0123456789abcdef",
        paths=(
            "app/code/Acme/Checkout/Controller/Index/Save.php",
            "app/code/Acme/Checkout/etc/di.xml",
            "app/code/Acme/Checkout/etc/events.xml",
            "app/etc/config.php",
            "bin/magento",
            "composer.json",
        ),
        marker_contents={
            "composer.json": '{"require":{"magento/framework":"*"}}',
        },
    )


def test_discovers_and_selects_php_and_magento_deterministically():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())

    assert set(catalog.registry.ordered_ids) == {
        "bash", "c", "c-sharp", "cpp", "css", "go", "haskell", "html",
        "data-contracts", "fastapi", "hyva", "java", "javascript", "json", "magento", "php", "python", "ruby", "spring",
        "rust", "scala", "tsx", "typescript",
    }
    assert capabilities.repository_plugins == ("json", "php", "magento")
    assert capabilities.file_plugins == {
        "app/code/Acme/Checkout/Controller/Index/Save.php": ("php",),
        "app/etc/config.php": ("php",),
        "composer.json": ("json",),
    }
    assert ProjectSelector(catalog.registry).select(_facts()) == capabilities
    assert runtime.repository_analysis_plugins(capabilities) == ("php", "magento")


def test_magento_is_not_selected_for_an_unrelated_composer_php_repository():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    facts = RepositoryFacts(
        revision="0123456789abcdef",
        paths=("composer.json", "src/File.php"),
        marker_contents={"composer.json": '{"require":{"magento/framework":"*"}}'},
    )

    capabilities = ProjectSelector(catalog.registry).select(facts)

    assert capabilities.repository_plugins == ("json", "php")


def test_only_selected_repository_plugins_require_snapshots():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=("config.json", "src/service.py"),
    ))

    assert capabilities.repository_plugins == ("json", "python")
    assert runtime.repository_analysis_plugins(capabilities) == ("python",)


def test_builtin_review_contributions_satisfy_the_deterministic_contract():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    paths = (
        "pom.xml",
        "requirements.txt",
        "src/App.java",
        "src/app.js",
        "src/main.py",
    )
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=paths,
        marker_contents={
            "pom.xml": "<dependency>org.springframework</dependency>",
            "requirements.txt": "fastapi==0.116.0",
        },
    ))

    contribution, diagnostics = runtime.review_contribution(
        ("src/App.java", "src/app.js", "src/main.py"),
        capabilities,
    )

    assert diagnostics == ()
    assert {
        request.kind for request in contribution.evidence_requests
    } == {
        "fastapi-component",
        "java-file",
        "python-file",
        "spring-component",
    }


def test_php_review_rules_distinguish_exact_call_returns_from_unknown_contracts():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    plugin = catalog.implementation("php")

    outcome = plugin.review(("src/Service.php",))

    assert outcome.value is not None
    assert any(
        "exact-call-return" in rule
        and "targetDeclaredReturnType" in rule
        and "absent contract attributes as unknown" in rule
        for rule in outcome.value.rules
    )


def test_language_validator_owns_normal_category_through_typed_claim_kind():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectCapabilities(
        repository_plugins=("java",),
        file_plugins={"src/App.java": ("java",)},
        detection_evidence={},
        fingerprint="sha256:" + ("0" * 64),
    )
    fact = GraphFact(
        "java-type",
        "example.App",
        "declares",
        "App",
        "src/App.java",
        1,
    )

    decisions = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/App.java",
            line=1,
            message="The declared Java type violates the expected contract.",
            evidence=(fact,),
            claim_kind="java-file",
        ),
        capabilities,
    )

    assert len(decisions) == 1
    assert decisions[0].decision is ValidationDecision.PASS


def test_php_repository_claim_requires_matching_cited_relation():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectCapabilities(
        repository_plugins=("php",),
        file_plugins={
            "src/BaseService.php": ("php",),
            "src/Service.php": ("php",),
        },
        detection_evidence={},
        fingerprint="sha256:" + ("0" * 64),
    )
    inheritance = GraphFact(
        "php-inheritance",
        "Acme\\Service",
        "extends",
        "Acme\\BaseService",
        "src/Service.php",
        5,
        related_paths=("src/BaseService.php",),
    )
    static_call = GraphFact(
        "php-static-call-relation",
        "Acme\\Service",
        "calls-static",
        "Acme\\BaseService",
        "src/Service.php",
        8,
        attributes=(("targetMethod", "prepare"),),
        related_paths=("src/BaseService.php",),
    )
    construction = GraphFact(
        "php-construction-relation",
        "Acme\\Service",
        "constructs",
        "Acme\\BaseService",
        "src/Service.php",
        9,
        related_paths=("src/BaseService.php",),
    )
    instance_call = GraphFact(
        "php-instance-call-relation",
        "Acme\\Service",
        "calls-instance",
        "Acme\\BaseService",
        "src/Service.php",
        10,
        attributes=(("targetMethod", "execute"),),
        related_paths=("src/BaseService.php",),
    )

    supported = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/Service.php",
            line=5,
            message="Acme\\Service extends Acme\\BaseService, so the parent contract affects this change.",
            evidence=(inheritance,),
            claim_kind="php-inheritance",
        ),
        capabilities,
    )
    mismatched = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/Service.php",
            line=5,
            message="Acme\\Service has a constructor dependency that affects this change.",
            evidence=(inheritance,),
            claim_kind="php-constructor-dependency",
        ),
        capabilities,
    )
    contradicted = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/Service.php",
            line=5,
            message="Acme\\Service does not extend Acme\\BaseService.",
            evidence=(inheritance,),
            claim_kind="php-inheritance",
        ),
        capabilities,
    )
    supported_call = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/Service.php",
            line=8,
            message=(
                "Acme\\Service calls Acme\\BaseService::prepare, so its "
                "static contract affects this change."
            ),
            evidence=(static_call,),
            claim_kind="php-static-call-relation",
        ),
        capabilities,
    )
    contradicted_call = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/Service.php",
            line=8,
            message="Acme\\Service does not call Acme\\BaseService::prepare.",
            evidence=(static_call,),
            claim_kind="php-static-call-relation",
        ),
        capabilities,
    )
    supported_construction = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/Service.php",
            line=9,
            message="Acme\\Service constructs Acme\\BaseService on this path.",
            evidence=(construction,),
            claim_kind="php-construction-relation",
        ),
        capabilities,
    )
    contradicted_construction = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/Service.php",
            line=9,
            message="Acme\\Service does not construct Acme\\BaseService.",
            evidence=(construction,),
            claim_kind="php-construction-relation",
        ),
        capabilities,
    )
    supported_instance_call = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/Service.php",
            line=10,
            message=(
                "Acme\\Service calls Acme\\BaseService::execute through its "
                "injected dependency."
            ),
            evidence=(instance_call,),
            claim_kind="php-instance-call-relation",
        ),
        capabilities,
    )
    contradicted_instance_call = runtime.validate(
        CandidateClaim(
            category="bug-risk",
            path="src/Service.php",
            line=10,
            message="Acme\\Service does not call Acme\\BaseService::execute.",
            evidence=(instance_call,),
            claim_kind="php-instance-call-relation",
        ),
        capabilities,
    )

    assert [result.decision for result in supported] == [
        ValidationDecision.INSUFFICIENT_EVIDENCE
    ]
    assert supported[0].code == "php-presence-not-defect-proof"
    assert [result.decision for result in mismatched] == [
        ValidationDecision.INSUFFICIENT_EVIDENCE
    ]
    assert [result.decision for result in contradicted] == [
        ValidationDecision.REJECT
    ]
    assert [result.decision for result in supported_call] == [
        ValidationDecision.INSUFFICIENT_EVIDENCE
    ]
    assert supported_call[0].code == "php-presence-not-defect-proof"
    assert [result.decision for result in contradicted_call] == [
        ValidationDecision.REJECT
    ]
    assert [result.decision for result in supported_construction] == [
        ValidationDecision.INSUFFICIENT_EVIDENCE
    ]
    assert supported_construction[0].code == "php-presence-not-defect-proof"
    assert [result.decision for result in contradicted_construction] == [
        ValidationDecision.REJECT
    ]
    assert [result.decision for result in supported_instance_call] == [
        ValidationDecision.INSUFFICIENT_EVIDENCE
    ]
    assert supported_instance_call[0].code == "php-presence-not-defect-proof"
    assert [result.decision for result in contradicted_instance_call] == [
        ValidationDecision.REJECT
    ]


def test_review_evidence_cap_does_not_starve_framework_kinds():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    paths = tuple(f"src/main/java/example/Service{index:03d}.java" for index in range(100))
    capabilities = ProjectCapabilities(
        repository_plugins=("java", "spring"),
        file_plugins={path: ("java", "spring") for path in paths},
        detection_evidence={},
        fingerprint="sha256:" + ("0" * 64),
    )

    contribution, diagnostics = runtime.review_contribution(paths, capabilities)

    assert diagnostics == ()
    assert len(contribution.evidence_requests) == runtime.MAX_EVIDENCE_REQUESTS
    counts = {
        kind: sum(
            request.kind == kind
            for request in contribution.evidence_requests
        )
        for kind in ("java-file", "spring-component")
    }
    assert counts == {"java-file": 40, "spring-component": 40}


def test_magento_selects_installed_application_without_direct_framework_requirement():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    facts = RepositoryFacts(
        revision="0123456789abcdef",
        paths=("app/etc/config.php", "bin/magento", "composer.json"),
        marker_contents={"composer.json": '{"require":{"magento/product-community-edition":"*"}}'},
    )

    assert ProjectSelector(catalog.registry).select(facts).repository_plugins == ("json", "php", "magento")


def test_magento_selects_standalone_and_nested_module_workspaces():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    standalone = RepositoryFacts(
        revision="0123456789abcdef",
        paths=("composer.json", "etc/module.xml", "registration.php"),
    )
    nested = RepositoryFacts(
        revision="0123456789abcdef",
        paths=(
            "composer.json",
            "packages/acme-checkout/etc/module.xml",
            "packages/acme-checkout/registration.php",
        ),
    )

    assert ProjectSelector(catalog.registry).select(standalone).repository_plugins == ("json", "php", "magento")
    assert ProjectSelector(catalog.registry).select(nested).repository_plugins == ("json", "php", "magento")


def test_php_emits_namespace_type_and_relationship_facts():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    artifact = FileArtifact(
        "app/code/Acme/Checkout/Controller/Index/Save.php",
        """<?php
namespace Acme\\Checkout\\Controller\\Index;
use Magento\\Framework\\App\\Action\\Action;
final class Save extends Action implements HttpPostActionInterface {
    use ValidationTrait;
    public function execute() { return new ResultFactory(); }
}
""",
    )

    facts, diagnostics = runtime.graph_facts(artifact, capabilities)

    assert diagnostics == ()
    assert ("php-inheritance", "extends", "Action") in {
        (fact.kind, fact.relation, fact.target) for fact in facts
    }
    assert ("php-trait", "uses-trait", "ValidationTrait") in {
        (fact.kind, fact.relation, fact.target) for fact in facts
    }
    assert ("php-construction", "constructs", "ResultFactory") in {
        (fact.kind, fact.relation, fact.target) for fact in facts
    }


def test_magento_emits_effective_di_from_repository_state_only():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    artifacts = tuple(sorted((
        FileArtifact(
            "app/code/Acme/Checkout/etc/di.xml",
            """<?xml version="1.0"?>
<config>
  <preference for="Acme\\Api\\CartInterface" type="Acme\\Model\\Cart" />
  <virtualType name="Acme\\VirtualCart" type="Acme\\Model\\Cart" />
  <type name="Acme\\Model\\Cart">
    <plugin name="audit" type="Acme\\Plugin\\Audit" sortOrder="10" />
  </type>
</config>
""",
        ),
        FileArtifact(
            "app/code/Acme/Checkout/etc/module.xml",
            '<config><module name="Acme_Checkout" /></config>',
        ),
        FileArtifact(
            "app/etc/config.php",
            "<?php return ['modules' => ['Acme_Checkout' => 1]];",
        ),
    ), key=lambda artifact: artifact.path))

    per_file_facts, per_file_diagnostics = runtime.graph_facts(
        artifacts[0], capabilities
    )
    assert per_file_facts == ()
    assert per_file_diagnostics == ()

    handle = runtime.start_repository_analysis(capabilities, "0123456789abcdef")
    handle.ingest(artifacts)
    analysis, diagnostics = handle.finish()

    assert diagnostics == ()
    facts = tuple(fact for packet in analysis.packets for fact in packet.facts)
    triples = {(fact.kind, fact.relation, fact.target) for fact in facts}
    assert (
        "magento-di-effective-preference",
        "resolves-to",
        "Acme\\Model\\Cart",
    ) in triples
    assert any(
        fact.kind == "magento-di-effective-plugin"
        and fact.target == "Acme\\Plugin\\Audit"
        for fact in facts
    )


def test_magento_quarantines_unsafe_config_without_failing_repository_analysis():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    artifact = FileArtifact(
        "app/code/Acme/Checkout/etc/di.xml",
        '<!DOCTYPE config [<!ENTITY secret SYSTEM "file:///etc/passwd">]><config>&secret;</config>',
    )

    handle = runtime.start_repository_analysis(capabilities, "0123456789abcdef")
    handle.ingest(tuple(sorted((
        artifact,
        FileArtifact(
            "app/code/Acme/Checkout/etc/module.xml",
            '<config><module name="Acme_Checkout" /></config>',
        ),
        FileArtifact(
            "app/etc/config.php",
            "<?php return ['modules' => ['Acme_Checkout' => 1]];",
        ),
    ), key=lambda value: value.path)))
    analysis, diagnostics = handle.finish()

    assert analysis.snapshots
    assert all(
        artifact.path not in packet.paths
        for packet in analysis.packets
    )
    assert [diagnostic.code for diagnostic in diagnostics] == ["magento-unsafe-xml"]
    assert diagnostics[0].recoverable is True
    assert diagnostics[0].path == artifact.path


def test_magento_keeps_custom_entity_bearing_xml_outside_architecture_analysis():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    path = "app/code/Punchout/Gateway/etc/samples/cxml_inv_po.xml"
    artifact = FileArtifact(
        path,
        """<?xml version="1.0"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.014/InvoiceDetail.dtd">
<cXML payloadID="sample"><Response /></cXML>
""",
    )

    assert runtime.file_disposition(
        path,
        capabilities,
    ) is FileDisposition.FULL

    handle = runtime.start_repository_analysis(
        capabilities,
        "0123456789abcdef",
    )
    handle.ingest(tuple(sorted((
        artifact,
        FileArtifact(
            "app/code/Punchout/Gateway/etc/module.xml",
            '<config><module name="Punchout_Gateway" /></config>',
        ),
        FileArtifact(
            "app/etc/config.php",
            "<?php return ['modules' => ['Punchout_Gateway' => 1]];",
        ),
    ), key=lambda value: value.path)))
    analysis, diagnostics = handle.finish()

    assert diagnostics == ()
    assert all(
        path not in packet.paths
        for packet in analysis.packets
    )


def test_magento_file_policy_keeps_architecture_without_vendor_test_vectors():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())

    assert runtime.file_disposition(
        "vendor/magento/module-catalog/etc/di.xml", capabilities
    ) is FileDisposition.ARCHITECTURE_ONLY
    assert runtime.file_disposition(
        "vendor/magento/module-catalog/Model/Product.php", capabilities
    ) is FileDisposition.FULL
    assert runtime.file_disposition(
        "vendor/magento/module-catalog/Test/Unit/ProductTest.php", capabilities
    ) is FileDisposition.EXCLUDED
    assert runtime.file_disposition(
        "generated/code/Magento/Catalog/Model/Product/Proxy.php", capabilities
    ) is FileDisposition.GENERATED
    assert runtime.file_disposition(
        "pub/static/frontend/Acme/theme/en_US/app.js", capabilities
    ) is FileDisposition.GENERATED
    assert runtime.file_disposition(
        "dev/tests/integration/testsuite/Acme/Fixture.php", capabilities
    ) is FileDisposition.EXCLUDED


def test_magento_validator_abstains_without_typed_proof():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    claim = CandidateClaim(
        category="magento-di-preference",
        path="app/code/Acme/Checkout/etc/di.xml",
        line=2,
        message="The preference points to the wrong implementation.",
    )

    decisions = runtime.validate(claim, capabilities)

    assert len(decisions) == 1
    assert decisions[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert decisions[0].code == "magento-evidence-unavailable"


def test_magento_generated_factory_claim_requires_matching_exact_fact_kind():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    factory_fact = GraphFact(
        "magento-generated-factory-resolution",
        "Acme\\Checkout\\Model\\Service",
        "creates-via-generated-factory",
        "Acme\\Checkout\\Model\\Item",
        "app/code/Acme/Checkout/Model/Service.php",
        7,
        (
            ("area", "global"),
            (
                "factoryType",
                "Acme\\Checkout\\Model\\ItemFactory",
            ),
        ),
        ("app/code/Acme/Checkout/Model/Item.php",),
    )
    base = {
        "category": "bug-risk",
        "path": "app/code/Acme/Checkout/Model/Service.php",
        "line": 7,
        "message": (
            "The generated ItemFactory creates Item in the global area."
        ),
        "evidence": (factory_fact,),
    }

    supported = runtime.validate(
        CandidateClaim(
            **base,
            claim_kind="magento-generated-factory-resolution",
        ),
        capabilities,
    )
    mismatched = runtime.validate(
        CandidateClaim(
            **base,
            claim_kind="magento-generated-factory",
        ),
        capabilities,
    )

    assert supported[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert supported[0].code == "magento-presence-not-defect-proof"
    assert mismatched[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert mismatched[0].code == "magento-cited-evidence-mismatch"


def test_magento_generated_proxy_claim_requires_matching_exact_fact_kind():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    proxy_fact = GraphFact(
        "magento-generated-proxy-resolution",
        "Acme\\Checkout\\Model\\Service",
        "lazy-loads-via-generated-proxy",
        "Acme\\Checkout\\Model\\Item",
        "app/code/Acme/Checkout/etc/di.xml",
        7,
        (
            ("area", "global"),
            (
                "proxyType",
                "Acme\\Checkout\\Api\\ItemInterface\\Proxy",
            ),
        ),
        ("app/code/Acme/Checkout/Model/Item.php",),
    )
    base = {
        "category": "bug-risk",
        "path": "app/code/Acme/Checkout/etc/di.xml",
        "line": 7,
        "message": (
            "The generated ItemInterface proxy lazy-loads Item globally."
        ),
        "evidence": (proxy_fact,),
    }

    supported = runtime.validate(
        CandidateClaim(
            **base,
            claim_kind="magento-generated-proxy-resolution",
        ),
        capabilities,
    )
    mismatched = runtime.validate(
        CandidateClaim(
            **base,
            claim_kind="magento-generated-proxy",
        ),
        capabilities,
    )

    assert supported[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert supported[0].code == "magento-presence-not-defect-proof"
    assert mismatched[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert mismatched[0].code == "magento-cited-evidence-mismatch"


def test_magento_plugin_owns_framework_claim_classification():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    claim = CandidateClaim(
        category="bug-risk",
        path="app/code/Acme/Checkout/etc/di.xml",
        line=2,
        message="The Magento preference points to the wrong implementation.",
    )

    decisions = runtime.validate(claim, capabilities)

    assert len(decisions) == 1
    assert decisions[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE


def test_magento_interceptor_order_claim_requires_exact_priority_evidence():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    base = {
        "category": "bug-risk",
        "path": "app/code/Acme/Checkout/etc/di.xml",
        "line": 2,
        "message": (
            "The Magento interceptor execution order is wrong because "
            "Audit runs before Guard."
        ),
    }
    generic_plugin_fact = GraphFact(
        "magento-di-effective-plugin",
        "Acme\\Checkout\\Model\\Cart",
        "intercepted-by",
        "Acme\\Checkout\\Plugin\\Audit",
        "app/code/Acme/Checkout/etc/di.xml",
        2,
    )
    priority_fact = GraphFact(
        "magento-di-plugin-priority",
        "Acme\\Checkout\\Plugin\\Audit",
        "prioritized-before",
        "Acme\\Checkout\\Plugin\\Guard",
        "app/code/Acme/Checkout/etc/di.xml",
        2,
    )

    mismatch = runtime.validate(
        CandidateClaim(**base, evidence=(generic_plugin_fact,)),
        capabilities,
    )
    supported = runtime.validate(
        CandidateClaim(**base, evidence=(priority_fact,)),
        capabilities,
    )

    assert mismatch[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert mismatch[0].code == "magento-cited-evidence-mismatch"
    assert supported[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert supported[0].code == "magento-presence-not-defect-proof"


def test_magento_validator_rejects_execution_claim_for_inapplicable_interceptor():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    config_fact = GraphFact(
        "magento-di-effective-plugin",
        "Acme\\Checkout\\Model\\FinalCart",
        "intercepted-by",
        "Acme\\Checkout\\Plugin\\Audit",
        "app/code/Acme/Checkout/etc/di.xml",
        2,
    )
    inapplicable_fact = GraphFact(
        "magento-interceptor-inapplicable",
        "Acme\\Checkout\\Plugin\\Audit",
        "cannot-intercept",
        "Acme\\Checkout\\Model\\FinalCart::save",
        "app/code/Acme/Checkout/etc/di.xml",
        2,
        (("reason", "final-class"),),
    )
    claim = CandidateClaim(
        category="bug-risk",
        path="app/code/Acme/Checkout/etc/di.xml",
        line=2,
        message=(
            "The Magento interceptor Audit intercepts FinalCart::save and "
            "changes the persisted value."
        ),
        evidence=tuple(sorted((config_fact, inapplicable_fact))),
    )

    decisions = runtime.validate(claim, capabilities)

    assert decisions[0].decision is ValidationDecision.REJECT
    assert (
        decisions[0].code
        == "magento-interceptor-inapplicable-contradiction"
    )

    defect_claim = CandidateClaim(
        category="bug-risk",
        path="app/code/Acme/Checkout/etc/di.xml",
        line=2,
        message=(
            "The Magento interceptor Audit cannot intercept FinalCart::save "
            "because FinalCart is final, so the configured behavior never runs."
        ),
        evidence=(inapplicable_fact,),
        claim_kind="magento-interceptor-inapplicable",
    )

    defect_decisions = runtime.validate(defect_claim, capabilities)

    assert defect_decisions[0].decision is ValidationDecision.PASS
    assert (
        defect_decisions[0].code
        == "magento-exact-defect-proof-present"
    )


def test_magento_diagnostic_fact_requires_matching_path_and_identifier():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    diagnostic = GraphFact(
        "magento-message-consumer-invalid",
        "acme.invalid.consumer",
        "has-empty-queue",
        "acme.invalid.consumer",
        "app/code/Acme/Checkout/etc/queue_consumer.xml",
        3,
    )
    base = {
        "category": "bug-risk",
        "line": 3,
        "evidence": (diagnostic,),
        "claim_kind": "magento-message-consumer-invalid",
    }

    wrong_identifier = runtime.validate(
        CandidateClaim(
            **base,
            path="app/code/Acme/Checkout/etc/queue_consumer.xml",
            message="The unrelated.consumer has an empty queue.",
        ),
        capabilities,
    )
    wrong_path = runtime.validate(
        CandidateClaim(
            **base,
            path="app/code/Acme/Checkout/etc/di.xml",
            message="The acme.invalid.consumer has an empty queue.",
        ),
        capabilities,
    )

    assert wrong_identifier[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert wrong_identifier[0].code == "magento-cited-identifier-mismatch"
    assert wrong_path[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert wrong_path[0].code == "magento-cited-evidence-mismatch"


def test_php_validator_does_not_claim_generic_candidates():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())
    claim = CandidateClaim(
        category="bug-risk",
        path="app/code/Acme/Checkout/Controller/Index/Save.php",
        line=2,
        message="The return value is ignored.",
    )

    assert runtime.validate(claim, capabilities) == ()


def test_review_contributions_are_bounded_and_add_no_model_api():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(_facts())

    contribution, diagnostics = runtime.review_contribution(_facts().paths, capabilities)

    assert diagnostics == ()
    assert 1 <= len(contribution.rules) <= runtime.MAX_RULES
    assert 1 <= len(contribution.evidence_requests) <= runtime.MAX_EVIDENCE_REQUESTS
    assert not any(hasattr(plugin, "llm") or hasattr(plugin, "model_client") for plugin in catalog.implementations.values())
