from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from codecrow_plugins import (
    CandidateClaim,
    FileArtifact,
    OutcomeStatus,
    PluginRegistry,
    ProjectSelector,
    RepositoryFacts,
    ValidationDecision,
    load_descriptor,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]
QUARKUS_ROOT = PLUGINS_ROOT / "frameworks/quarkus"
JAVA_DESCRIPTOR = PLUGINS_ROOT / "languages/java/plugin.json"
QUARKUS_DESCRIPTOR = QUARKUS_ROOT / "plugin.json"
JAVA_PATH = "services/catalog/src/main/java/example/ItemResource.java"


def _plugin():
    module_name = "_codecrow_test_quarkus"
    module = sys.modules.get(module_name)
    if module is None:
        package = QUARKUS_ROOT / "python/codecrow_plugin_quarkus"
        spec = importlib.util.spec_from_file_location(
            module_name,
            package / "__init__.py",
            submodule_search_locations=[str(package)],
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module.create_plugin(load_descriptor(QUARKUS_DESCRIPTOR))


def _selector() -> ProjectSelector:
    return ProjectSelector(PluginRegistry((
        load_descriptor(JAVA_DESCRIPTOR),
        load_descriptor(QUARKUS_DESCRIPTOR),
    )))


@pytest.mark.parametrize(
    ("build_file", "content"),
    (
        (
            "pom.xml",
            "<dependency><groupId>io.quarkus</groupId></dependency>",
        ),
        ("build.gradle", "plugins { id 'io.quarkus' }"),
        ("build.gradle.kts", 'plugins { id("io.quarkus") }'),
    ),
)
def test_detects_nested_quarkus_builds_at_one_coherent_root(
    build_file: str,
    content: str,
):
    build_path = f"services/catalog/{build_file}"
    capabilities = _selector().select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=tuple(sorted((build_path, JAVA_PATH))),
        marker_contents={build_path: content},
    ))

    assert capabilities.repository_plugins == ("java", "quarkus")
    assert "root:services/catalog" in capabilities.detection_evidence["quarkus"]


def test_java_marker_does_not_cross_an_unrelated_build_root():
    build_path = "services/unrelated/pom.xml"
    marker_path = "services/catalog/src/main/java/example/Main.java"
    capabilities = _selector().select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=tuple(sorted((build_path, marker_path))),
        marker_contents={
            build_path: "<project/>",
            marker_path: "import io.quarkus.runtime.Quarkus;",
        },
    ))

    assert capabilities.repository_plugins == ("java",)


def test_indexes_exact_quarkus_java_relationships():
    source = '''package example;
import io.quarkus.hibernate.orm.panache.PanacheEntity;
import io.quarkus.hibernate.orm.panache.PanacheRepository;
import io.quarkus.scheduler.Scheduled;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import org.eclipse.microprofile.config.inject.ConfigProperty;
import org.eclipse.microprofile.reactive.messaging.Incoming;
import org.eclipse.microprofile.reactive.messaging.Outgoing;

@ApplicationScoped
@Path("/items")
public class ItemResource extends PanacheEntity {
    @Inject ItemService service;
    @ConfigProperty(name = "item.limit", defaultValue = "10") int limit;

    @Inject
    ItemResource(ItemRepository repository) {}

    @GET
    @Path("/{id}")
    String get() { return "ok"; }

    @Scheduled(every = "10s", delayed = "1s")
    void refresh() {}

    @Incoming("prices")
    @Outgoing("quotes")
    String relay(String value) { return value; }
}

interface ItemRepository extends PanacheRepository<ItemResource> {}
'''
    outcome = _plugin().index_file(FileArtifact(JAVA_PATH, source))

    assert outcome.status is OutcomeStatus.HANDLED
    facts = outcome.value
    assert facts == tuple(sorted(facts))
    assert {
        (fact.kind, fact.source, fact.relation, fact.target)
        for fact in facts
    } >= {
        (
            "quarkus-cdi-bean",
            "example.ItemResource",
            "scoped-as",
            "ApplicationScoped",
        ),
        (
            "quarkus-cdi-injection",
            "example.ItemResource",
            "depends-on",
            "ItemService",
        ),
        (
            "quarkus-cdi-injection",
            "example.ItemResource",
            "depends-on",
            "ItemRepository",
        ),
        (
            "quarkus-config-property",
            "example.ItemResource#limit",
            "reads",
            "item.limit",
        ),
        (
            "quarkus-jaxrs-resource",
            "example.ItemResource",
            "serves",
            "/items",
        ),
        (
            "quarkus-jaxrs-route",
            "example.ItemResource#get",
            "handles",
            "GET /items/{id}",
        ),
        (
            "quarkus-panache-entity",
            "example.ItemResource",
            "extends",
            "io.quarkus.hibernate.orm.panache.PanacheEntity",
        ),
        (
            "quarkus-panache-repository",
            "example.ItemRepository",
            "manages",
            "ItemResource",
        ),
        (
            "quarkus-reactive-channel",
            "example.ItemResource#relay",
            "consumes",
            "prices",
        ),
        (
            "quarkus-reactive-channel",
            "example.ItemResource#relay",
            "produces",
            "quotes",
        ),
        (
            "quarkus-scheduled-method",
            "example.ItemResource#refresh",
            "runs-on",
            "every=10s",
        ),
    }
    scheduled = next(
        fact for fact in facts if fact.kind == "quarkus-scheduled-method"
    )
    assert scheduled.attributes == (("delayed", "1s"), ("every", "10s"))


def test_annotation_short_names_require_relevant_imports():
    source = '''package example;
@ApplicationScoped
@Path("/not-quarkus")
class CustomType {
    @Scheduled(every = "1s") void run() {}
}
'''

    assert _plugin().index_file(FileArtifact(JAVA_PATH, source)).status is (
        OutcomeStatus.ABSTAINED
    )


@pytest.mark.parametrize(
    "source",
    (
        '''package example;
import jakarta.inject.Inject;
class Plain {
    @interface Inject {}
    @Inject ItemService service;
}
''',
        '''package example;
import jakarta.ws.rs.*;
@Path("/items")
class ItemResource {
    @GET String get() { return "ok"; }
}
''',
    ),
)
def test_annotations_abstain_for_local_shadowing_and_wildcard_imports(source: str):
    outcome = _plugin().index_file(FileArtifact(JAVA_PATH, source))

    assert outcome.status is OutcomeStatus.ABSTAINED


def test_malformed_java_returns_a_recoverable_diagnostic_without_partial_facts():
    source = '''package example;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
@Path("/items") class ItemResource {
    @GET String get( {
}
'''

    outcome = _plugin().index_file(FileArtifact(JAVA_PATH, source))

    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.value is None
    assert outcome.diagnostic.code == "quarkus-java-parse-incomplete"
    assert outcome.diagnostic.recoverable is True
    assert outcome.diagnostic.path == JAVA_PATH


def test_indexes_only_safe_bounded_application_property_keys():
    path = "services/catalog/src/main/resources/application.properties"
    content = "\n".join((
        "# comments are ignored",
        "quarkus.http.port=8080",
        "%test.quarkus.datasource.db-kind: postgresql",
        "greeting.message = hello",
        "continued.value=secret\\",
        "  continuation-that-is-not-a-key",
        "unsupported whitespace key = ignored",
        "",
    ))

    outcome = _plugin().index_file(FileArtifact(path, content))

    assert outcome.status is OutcomeStatus.HANDLED
    assert {(fact.target, fact.line) for fact in outcome.value} == {
        ("%test.quarkus.datasource.db-kind", 3),
        ("greeting.message", 4),
        ("quarkus.http.port", 2),
    }
    assert all("postgresql" not in repr(fact) for fact in outcome.value)
    profiled = next(fact for fact in outcome.value if fact.target.startswith("%"))
    assert profiled.attributes == (("profile", "test"),)

    bounded = _plugin().index_file(FileArtifact(
        path,
        "\n".join(f"key.{index}=value" for index in range(140)),
    ))
    assert len(bounded.value) == 128


def test_review_requests_exact_facts_and_validation_never_promotes_topology():
    plugin = _plugin()
    source = '''package example;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
@Path("/items") class ItemResource {
    @GET @Path("/{id}") String get() { return "ok"; }
}
'''
    route = next(
        fact
        for fact in plugin.index_file(FileArtifact(JAVA_PATH, source)).value
        if fact.kind == "quarkus-jaxrs-route"
    )
    review = plugin.review((
        JAVA_PATH,
        "services/catalog/src/main/resources/application.properties",
        "README.md",
    ))

    assert review.status is OutcomeStatus.HANDLED
    assert tuple(
        request.identifier for request in review.value.evidence_requests
    ) == (
        JAVA_PATH,
        "services/catalog/src/main/resources/application.properties",
    )

    contradicted = plugin.validate(CandidateClaim(
        category="framework-risk",
        path=JAVA_PATH,
        line=5,
        message="The GET /items/{id} route is missing.",
        evidence=(route,),
        claim_kind="quarkus-jaxrs-route",
    ))
    topology_only = plugin.validate(CandidateClaim(
        category="framework-risk",
        path=JAVA_PATH,
        line=5,
        message="GET /items/{id} lacks an authorization check.",
        evidence=(route,),
        claim_kind="quarkus-jaxrs-route",
    ))
    unrelated_absence = plugin.validate(CandidateClaim(
        category="framework-risk",
        path=JAVA_PATH,
        line=5,
        message=(
            "Authorization is missing from GET /items/{id}, so the route "
            "may expose data."
        ),
        evidence=(route,),
        claim_kind="quarkus-jaxrs-route",
    ))
    wrong_identifier = plugin.validate(CandidateClaim(
        category="framework-risk",
        path=JAVA_PATH,
        line=5,
        message="The GET /other route is missing.",
        evidence=(route,),
        claim_kind="quarkus-jaxrs-route",
    ))
    absence_for_other_route = plugin.validate(CandidateClaim(
        category="framework-risk",
        path=JAVA_PATH,
        line=5,
        message="No route exists for /other, while GET /items/{id} is slow.",
        evidence=(route,),
        claim_kind="quarkus-jaxrs-route",
    ))
    unknown_kind = plugin.validate(CandidateClaim(
        category="quarkus-cache",
        path=JAVA_PATH,
        line=5,
        message="The GET /items/{id} route is missing.",
        evidence=(route,),
        claim_kind="",
    ))

    assert contradicted.value.decision is ValidationDecision.REJECT
    assert topology_only.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert topology_only.value.code == "quarkus-topology-not-defect-proof"
    assert unrelated_absence.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert unrelated_absence.value.code == "quarkus-topology-not-defect-proof"
    assert wrong_identifier.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert wrong_identifier.value.code == "quarkus-cited-identifier-mismatch"
    assert absence_for_other_route.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert absence_for_other_route.value.code == "quarkus-topology-not-defect-proof"
    assert unknown_kind.value.decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert unknown_kind.value.code == "quarkus-unknown-fact-kind"
