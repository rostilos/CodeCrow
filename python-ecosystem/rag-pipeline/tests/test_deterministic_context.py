"""
Tests for rag_pipeline.services.deterministic_context — DeterministicContextMixin.

Covers:
- get_deterministic_context full workflow (Steps 1-4)
- _apply_branch_priority
- _query_changed_file
- _query_definitions
- _query_transitive_parents
- _query_class_context
- _query_namespace_context
- Edge cases: collection not found, errors in queries, deduplication
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from types import SimpleNamespace
from qdrant_client.http.models import FieldCondition, MatchValue


# ── Helper factories ──

def _mock_config(**overrides):
    cfg = MagicMock()
    cfg.qdrant_url = "http://localhost:6333"
    cfg.qdrant_api_key = None
    cfg.qdrant_collection_prefix = "rag"
    cfg.embedding_provider = "ollama"
    cfg.embedding_dim = 768
    cfg.embedding_supports_instructions = False
    cfg.ollama_model = "nomic-embed-text"
    cfg.ollama_base_url = "http://localhost:11434"
    cfg.openrouter_api_key = "sk-test"
    cfg.openrouter_model = "openai/text-embedding-3-small"
    cfg.openrouter_base_url = "https://openrouter.ai/api/v1"
    cfg.max_identifiers_per_query = 100
    cfg.max_parent_classes_per_query = 20
    cfg.max_namespaces_per_query = 10
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _make_point(payload, point_id="p1"):
    """Create a mock Qdrant point with the given payload."""
    p = SimpleNamespace()
    p.id = point_id
    p.payload = payload
    p.vector = [0.0] * 10
    return p


def _branch_filter(branch="main"):
    """Create a real FieldCondition usable as branch_filter."""
    return FieldCondition(key="branch", match=MatchValue(value=branch))


def _build_service(plugin_catalog=None):
    """Build a DeterministicContextMixin-bearing service with all deps mocked."""
    with patch("rag_pipeline.services.base.create_embedding_model") as mock_create, \
         patch("rag_pipeline.services.base.get_embedding_model_info") as mock_info, \
         patch("rag_pipeline.services.base.QdrantClient") as MockQdrant:

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()

        from rag_pipeline.services.base import RAGQueryBase
        from rag_pipeline.services.deterministic_context import DeterministicContextMixin

        class TestService(DeterministicContextMixin, RAGQueryBase):
            pass

        config = _mock_config()
        service = TestService(config, plugin_catalog=plugin_catalog)
        service._observe_branches = MagicMock()
        return service


# ─────────────────────────────────────────────────────────────
# _apply_branch_priority
# ─────────────────────────────────────────────────────────────
class TestApplyBranchPriority:

    def test_empty_points(self):
        svc = _build_service()
        result = svc._apply_branch_priority([], "main", ["main"], set())
        assert result == []

    def test_pr_points_take_priority(self):
        svc = _build_service()
        pr_pt = _make_point({"path": "a.java", "pr": True, "branch": "feat"}, "pr1")
        branch_pt = _make_point({"path": "a.java", "pr": False, "branch": "main"}, "br1")
        result = svc._apply_branch_priority([pr_pt, branch_pt], "main", ["main", "feat"], set())
        assert len(result) == 1
        assert result[0].payload["pr"] is True

    def test_target_branch_takes_priority_over_base(self):
        svc = _build_service()
        target_pt = _make_point({"path": "b.java", "branch": "feat"}, "t1")
        base_pt = _make_point({"path": "b.java", "branch": "main"}, "b1")
        result = svc._apply_branch_priority(
            [target_pt, base_pt], "feat", ["feat", "main"], set()
        )
        assert len(result) == 1
        assert result[0].payload["branch"] == "feat"

    def test_base_branch_included_when_path_not_in_target(self):
        svc = _build_service()
        base_pt = _make_point({"path": "c.java", "branch": "main"}, "b1")
        result = svc._apply_branch_priority(
            [base_pt], "feat", ["feat", "main"], set()
        )
        assert len(result) == 1

    def test_base_branch_excluded_when_path_in_target_branch_paths(self):
        svc = _build_service()
        base_pt = _make_point({"path": "c.java", "branch": "main"}, "b1")
        result = svc._apply_branch_priority(
            [base_pt], "feat", ["feat", "main"], {"c.java"}
        )
        assert len(result) == 0

    def test_single_branch_returns_all_non_pr(self):
        svc = _build_service()
        pt1 = _make_point({"path": "d.java", "branch": "main"}, "d1")
        pt2 = _make_point({"path": "d.java", "branch": "main"}, "d2")
        result = svc._apply_branch_priority([pt1, pt2], "main", ["main"], set())
        assert len(result) == 2

    def test_older_target_plugin_identity_remains_eligible(self):
        catalog = MagicMock()
        catalog.registry.fingerprint_for.return_value = "sha256:descriptor"
        catalog.implementation_fingerprint.return_value = "sha256:implementation"
        svc = _build_service(plugin_catalog=catalog)
        stale_target = _make_point({
            "path": "src/Service.java",
            "branch": "feature",
            "plugin_ids": ["java", "spring"],
            "plugin_descriptor_fingerprint": "sha256:old-descriptor",
            "plugin_implementation_fingerprint": "sha256:old",
            "index_representation_fingerprint": (
                svc.index_representation_fingerprint
            ),
        }, "target")
        fresh_base = _make_point({
            "path": "src/Service.java",
            "branch": "main",
            "plugin_ids": ["java", "spring"],
            "plugin_descriptor_fingerprint": "sha256:descriptor",
            "plugin_implementation_fingerprint": "sha256:implementation",
            "index_representation_fingerprint": (
                svc.index_representation_fingerprint
            ),
        }, "base")

        result = svc._apply_branch_priority(
            [stale_target, fresh_base],
            "feature",
            ["feature", "main"],
            set(),
        )

        assert [point.id for point in result] == ["target"]


# ─────────────────────────────────────────────────────────────
# _query_changed_file
# ─────────────────────────────────────────────────────────────
class TestQueryChangedFile:

    def test_exact_path_match(self):
        svc = _build_service()

        payload = {
            "text": "public class Foo {}",
            "path": "src/Foo.java",
            "branch": "main",
            "parent_class": "BaseClass",
            "namespace": "com.example",
            "imports": ["com.util.Helper"],
            "extends": ["BaseClass"],
            "implements": ["Runnable"],
            "referenced_types": ["Worker", "Request"],
            "calls": ["load", "get", "format"],
            "semantic_names": ["Foo"],
            "primary_name": "Foo",
        }
        pt = _make_point(payload, "p1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        branch_filter = _branch_filter()
        all_chunks = []
        identifiers = set()
        parent_classes = set()
        namespaces = set()
        imports_raw = set()
        extends_raw = set()
        seen_texts = set()
        target_branch_paths = set()
        changed_file_paths = set()

        result = svc._query_changed_file(
            "coll", branch_filter, "src/Foo.java", 10,
            ["main"], "main", seen_texts, target_branch_paths,
            changed_file_paths, identifiers, parent_classes,
            namespaces, imports_raw, extends_raw, all_chunks
        )

        assert len(result) == 1
        assert result[0]["_match_type"] == "changed_file"
        assert "BaseClass" in parent_classes
        assert "com.example" in namespaces
        assert "Helper" in imports_raw
        assert "BaseClass" in extends_raw
        assert "Runnable" in extends_raw
        assert "Worker" in extends_raw
        assert "Request" in extends_raw
        assert "load" in identifiers
        assert "format" in identifiers
        assert "get" not in identifiers
        assert "src/Foo.java" in changed_file_paths
        assert "main" == "main" and "src/Foo.java" in target_branch_paths

    def test_fallback_to_multi_segment_repository_suffix(self):
        svc = _build_service()

        payload = {
            "text": "class Bar {}",
            "path": "src/Bar.java",
            "branch": "main",
        }
        pt = _make_point(payload, "p2")
        # First scroll returns empty (exact path miss), second returns the point
        svc.qdrant_client.scroll.side_effect = [
            ([], None),
            ([pt], None),
        ]

        all_chunks = []
        result = svc._query_changed_file(
            "coll", _branch_filter(), "checkout/src/Bar.java", 10,
            ["main"], "main", set(), set(),
            set(), set(), set(), set(), set(), set(), all_chunks
        )
        assert len(result) == 1
        assert svc.qdrant_client.scroll.call_count == 2

    def test_fallback_never_accepts_same_basename_from_another_module(self):
        svc = _build_service()
        wrong_module = _make_point({
            "text": "Cart module DI",
            "path": "app/code/Acme/Cart/etc/di.xml",
            "branch": "main",
        }, "wrong-module")
        svc.qdrant_client.scroll.side_effect = [
            ([], None),
            ([wrong_module], None),
        ]

        result = svc._query_changed_file(
            "coll",
            _branch_filter(),
            "checkout/app/code/Acme/Checkout/etc/di.xml",
            10,
            ["main"],
            "main",
            set(),
            set(),
            set(),
            set(),
            set(),
            set(),
            set(),
            set(),
            [],
        )

        assert result == []

    def test_dedup_by_seen_texts(self):
        svc = _build_service()

        payload = {"text": "duplicate text", "path": "x.java", "branch": "main"}
        pt = _make_point(payload, "p3")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        seen = {"duplicate text"}
        all_chunks = []
        result = svc._query_changed_file(
            "coll", _branch_filter(), "x.java", 10,
            ["main"], "main", seen, set(),
            set(), set(), set(), set(), set(), set(), all_chunks
        )
        assert len(result) == 0

    def test_branch_priority_filtering(self):
        svc = _build_service()

        target_pt = _make_point({"text": "target", "path": "a.java", "branch": "feat"}, "t1")
        base_pt = _make_point({"text": "base", "path": "a.java", "branch": "main"}, "b1")
        svc.qdrant_client.scroll.return_value = ([target_pt, base_pt], None)

        all_chunks = []
        result = svc._query_changed_file(
            "coll", _branch_filter("feat"), "a.java", 10,
            ["feat", "main"], "feat", set(), set(),
            set(), set(), set(), set(), set(), set(), all_chunks
        )
        assert len(result) == 1
        assert result[0]["text"] == "target"

    def test_imports_parsing_multi_segment(self):
        svc = _build_service()

        payload = {
            "text": "code",
            "path": "x.java",
            "branch": "main",
            "imports": ["com.example.util.Helper;", "org.foo.Bar"],
        }
        pt = _make_point(payload, "p4")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        imports_raw = set()
        svc._query_changed_file(
            "coll", _branch_filter(), "x.java", 10,
            ["main"], "main", set(), set(),
            set(), set(), set(), set(), imports_raw, set(), []
        )
        assert "Helper" in imports_raw
        assert "Bar" in imports_raw


class TestArchitectureContext:

    def test_exact_architecture_packet_expands_to_concrete_related_file(self):
        svc = _build_service()
        packet = _make_point({
            "text": "[magento-di-effective-plugin] CartInterface intercepted-by CartAudit",
            "path": "__analysis_architecture__/magento/packet.context",
            "branch": "main",
            "architecture_key": "global:CartInterface:cart_audit",
            "architecture_paths": [
                "app/code/Acme/Checkout/etc/di.xml",
                "app/code/Acme/Checkout/Plugin/CartAudit.php",
            ],
            "architecture_identifiers": [
                "Acme\\Checkout\\Api\\CartInterface",
                "Acme\\Checkout\\Plugin\\CartAudit",
            ],
        }, "architecture")
        related = _make_point({
            "text": "class CartAudit { public function aroundSave() {} }",
            "path": "app/code/Acme/Checkout/Plugin/CartAudit.php",
            "branch": "main",
            "primary_name": "CartAudit",
        }, "related")
        svc.qdrant_client.scroll.side_effect = [
            ([packet], None),
            ([related], None),
        ]
        all_chunks = []
        architecture = {}
        related_files = {}
        identifiers = set()

        svc._query_architecture_context(
            "coll",
            _branch_filter(),
            ["app/code/Acme/Checkout/etc/di.xml"],
            10,
            ["main"],
            "main",
            set(),
            set(),
            set(),
            all_chunks,
            architecture,
            related_files,
            identifiers,
        )

        assert [chunk["_match_type"] for chunk in all_chunks] == [
            "architecture_relation",
            "architecture_related",
        ]
        assert "global:CartInterface:cart_audit" in architecture
        assert "app/code/Acme/Checkout/Plugin/CartAudit.php" in related_files
        assert {"CartInterface", "CartAudit"} <= identifiers

    def test_compacted_packet_is_focused_to_facts_touching_batch_path(self):
        svc = _build_service()
        changed_path = "app/code/Acme/Checkout/Plugin/CartAudit.php"
        selected_related = "app/code/Acme/Checkout/etc/di.xml"
        unrelated_related = "app/code/Other/Module/Plugin/Noise.php"
        packet = _make_point({
            "text": "unfocused storage text",
            "path": "__analysis_architecture__/magento/packet.context",
            "branch": "main",
            "architecture_plugin": "magento",
            "architecture_kind": "magento-interception",
            "architecture_key": "magento-interception:di.xml:0",
            "architecture_paths": [
                changed_path,
                selected_related,
                unrelated_related,
            ],
            "architecture_identifiers": ["Relevant", "Noise"],
            "plugin_graph_facts": [
                {
                    "kind": "magento-di-effective-plugin",
                    "source": "CartInterface",
                    "relation": "intercepted-by",
                    "target": "CartAudit",
                    "path": selected_related,
                    "line": 12,
                    "related_paths": [changed_path],
                    "attributes": {"area": "global"},
                    "packetKey": "global:cart-audit",
                },
                {
                    "kind": "magento-di-effective-plugin",
                    "source": "NoiseInterface",
                    "relation": "intercepted-by",
                    "target": "Noise",
                    "path": "app/code/Other/Module/etc/di.xml",
                    "line": 8,
                    "related_paths": [unrelated_related],
                    "attributes": {},
                    "packetKey": "global:noise",
                },
            ],
        }, "architecture")
        related = _make_point({
            "text": "<config>relevant DI source</config>",
            "path": selected_related,
            "branch": "main",
        }, "related")
        svc.qdrant_client.scroll.side_effect = [
            ([packet], None),
            ([related], None),
        ]
        chunks = []
        architecture = {}
        related_files = {}
        identifiers = set()

        stats = svc._query_architecture_context(
            "coll", _branch_filter(), [changed_path], 5,
            ["main"], "main", set(), set(), set(), chunks,
            architecture, related_files, identifiers,
        )

        relation = chunks[0]
        assert "CartInterface intercepted-by CartAudit" in relation["text"]
        assert "NoiseInterface" not in relation["text"]
        assert relation["metadata"]["architecture_paths"] == [
            changed_path,
            selected_related,
        ]
        assert len(relation["metadata"]["plugin_graph_facts"]) == 1
        assert set(related_files) == {selected_related}
        assert unrelated_related not in related_files
        assert {"CartInterface", "CartAudit"} <= identifiers
        assert "Noise" not in identifiers
        assert stats["packet_chunks"] == 1
        assert stats["related_chunks"] == 1

    def test_plugin_retrieval_identifier_prioritizes_exact_related_method(self):
        svc = _build_service()
        changed_path = "app/code/Acme/Ui/BannerOptions.php"
        target_path = "app/code/Acme/Model/BannerRepository.php"
        packet = _make_point({
            "text": "stored architecture packet",
            "path": "__analysis_architecture__/php/packet.context",
            "branch": "main",
            "architecture_plugin": "php",
            "architecture_kind": "php-code-relation",
            "architecture_key": "php-code-relation:BannerOptions.php:0",
            "architecture_paths": [changed_path, target_path],
            "plugin_graph_facts": [{
                "kind": "php-instance-call-relation",
                "source": "Acme\\Ui\\BannerOptions",
                "relation": "calls-instance",
                "target": "Acme\\Model\\BannerRepository",
                "path": changed_path,
                "line": 24,
                "related_paths": [target_path],
                "attributes": {
                    "retrievalIdentifier:targetMethod": "getList",
                    "targetDeclaredReturnType": "array",
                    "targetMethod": "getList",
                    "targetMethodDeclared": "true",
                    "targetMethodVisibility": "public",
                },
                "packetKey": changed_path,
            }],
        }, "architecture")
        unrelated = [
            _make_point({
                "text": f"function helper{index}() {{}}",
                "path": target_path,
                "branch": "main",
                "primary_name": f"helper{index}",
                "start_line": index + 1,
            }, f"helper-{index}")
            for index in range(5)
        ]
        exact_method = _make_point({
            "text": "function getList(): array { return $this->items; }",
            "path": target_path,
            "branch": "main",
            "primary_name": "getList",
            "start_line": 100,
        }, "get-list")
        svc.qdrant_client.scroll.side_effect = [
            ([packet], None),
            ([*unrelated, exact_method], None),
        ]
        chunks = []
        related_files = {}
        identifiers = set()

        svc._query_architecture_context(
            "coll", _branch_filter(), [changed_path], 2,
            ["main"], "main", set(), set(), set(), chunks,
            {}, related_files, identifiers,
        )

        assert related_files[target_path][0]["text"].startswith(
            "function getList"
        )
        assert "targetDeclaredReturnType=array" in chunks[0]["text"]
        assert (
            chunks[0]["metadata"]["plugin_graph_facts"][0]["attributes"][
                "targetDeclaredReturnType"
            ]
            == "array"
        )
        assert len(related_files[target_path]) == 2
        assert "getList" in identifiers

    def test_magento_template_global_fact_retrieves_helper_contract(self):
        svc = _build_service()
        caller_path = (
            "app/design/frontend/Acme/custom/Acme_Theme/templates/"
            "banner/caller.phtml"
        )
        helper_path = (
            "app/design/frontend/Acme/custom/Acme_Theme/templates/"
            "banner/helper.phtml"
        )
        packet = _make_point({
            "text": "stored architecture packet",
            "path": "__analysis_architecture__/magento/template.context",
            "branch": "main",
            "architecture_plugin": "magento",
            "architecture_kind": "magento-template-global",
            "architecture_key": "window.fixBannerExternalLinks",
            "architecture_paths": [caller_path, helper_path],
            "plugin_graph_facts": [{
                "kind": "magento-template-global-call",
                "source": "window.fixBannerExternalLinks",
                "relation": "calls-unique-co-declared-definition",
                "target": "window.fixBannerExternalLinks",
                "path": caller_path,
                "line": 22,
                "related_paths": [helper_path],
                "attributes": {
                    "definitionPath": helper_path,
                    "resolution": "exact-layout-source",
                    "retrievalIdentifier:0000": (
                        "window.fixBannerExternalLinks"
                    ),
                },
                "packetKey": "window.fixBannerExternalLinks",
            }],
        }, "template-global")
        helper = _make_point({
            "text": (
                "<script>function fixExternalLinks(items) { "
                "if (items instanceof NodeList || Array.isArray(items)) { "
                "items.forEach(fixExternalLinks); return; } } "
                "window.fixBannerExternalLinks = fixExternalLinks;</script>"
            ),
            "path": helper_path,
            "branch": "main",
            "start_line": 1,
        }, "helper-source")
        svc.qdrant_client.scroll.side_effect = [
            ([packet], None),
            ([helper], None),
        ]
        chunks = []
        related_files = {}

        svc._query_architecture_context(
            "coll", _branch_filter(), [caller_path], 1,
            ["main"], "main", set(), set(), set(), chunks,
            {}, related_files, set(),
        )

        assert (
            "calls-unique-co-declared-definition"
            in chunks[0]["text"]
        )
        assert (
            "items instanceof NodeList || Array.isArray(items)"
            in related_files[helper_path][0]["text"]
        )

    def test_hyva_webapi_fact_retrieves_exact_item_producer_method(self):
        svc = _build_service()
        consumer_path = (
            "app/design/frontend/Acme/custom/Acme_Sales/templates/"
            "orders/items.phtml"
        )
        initializer_path = (
            "app/design/frontend/Acme/custom/Acme_Sales/templates/"
            "orders/init.phtml"
        )
        producer_path = (
            "app/code/Acme/Sales/Model/OrderItemsProcessor.php"
        )
        service_path = "app/code/Acme/Sales/Model/OrderInfo.php"
        packet = _make_point({
            "text": "stored Hyva architecture packet",
            "path": "__analysis_architecture__/hyva/template.context",
            "branch": "main",
            "architecture_plugin": "hyva",
            "architecture_kind": "hyva-template-runtime",
            "architecture_key": "hyva-template-runtime:init.phtml:0",
            "architecture_paths": [
                consumer_path,
                initializer_path,
                producer_path,
                service_path,
            ],
            "plugin_graph_facts": [{
                "kind": "hyva-template-webapi-reference",
                "source": initializer_path,
                "relation": "references-exact-webapi-route-literal",
                "target": "POST /V1/acme/orders/list/",
                "path": initializer_path,
                "line": 19,
                "related_paths": [
                    consumer_path,
                    producer_path,
                    service_path,
                ],
                "attributes": {
                    "implementation": "Acme\\Sales\\Model\\OrderInfo",
                    "resolution": "exact-registry-route-literal",
                    "retrievalIdentifier:0000": "getOrderInfo",
                    "retrievalIdentifier:0001": "process",
                    "route": "/V1/acme/orders/list",
                    "service": (
                        "Acme\\Sales\\Api\\OrderInfoInterface::getOrderInfo"
                    ),
                },
                "packetKey": initializer_path,
            }],
        }, "hyva-template")
        helper = _make_point({
            "text": "private function helper(): void {}",
            "path": producer_path,
            "branch": "main",
            "primary_name": "helper",
            "start_line": 3,
        }, "producer-helper")
        producer = _make_point({
            "text": (
                "public function process(): array { "
                "return [['display_price' => '10.00']]; }"
            ),
            "path": producer_path,
            "branch": "main",
            "primary_name": "process",
            "start_line": 10,
        }, "producer-process")
        service = _make_point({
            "text": (
                "public function getOrderInfo(): array { "
                "return $this->orders->prepareOrdersData(); }"
            ),
            "path": service_path,
            "branch": "main",
            "primary_name": "getOrderInfo",
            "start_line": 20,
        }, "service")
        initializer = _make_point({
            "text": (
                "fetch('/rest/V1/acme/orders/list', {method: 'POST'})"
            ),
            "path": initializer_path,
            "branch": "main",
            "start_line": 19,
        }, "initializer")
        svc.qdrant_client.scroll.side_effect = [
            ([packet], None),
            ([helper, producer, service, initializer], None),
        ]
        chunks = []
        related_files = {}

        svc._query_architecture_context(
            "coll", _branch_filter(), [consumer_path], 1,
            ["main"], "main", set(), set(), set(), chunks,
            {}, related_files, set(),
        )

        assert (
            "references-exact-webapi-route-literal"
            in chunks[0]["text"]
        )
        assert "display_price" in related_files[producer_path][0]["text"]
        assert (
            related_files[producer_path][0]["metadata"]["primary_name"]
            == "process"
        )
        assert len(related_files[producer_path]) == 1

    def test_architecture_query_paginates_exact_matches(self):
        svc = _build_service()
        requested_path = "app/code/Acme/Checkout/etc/di.xml"
        first = _make_point({
            "text": "first relation",
            "path": "__analysis_architecture__/magento/first.context",
            "branch": "main",
            "architecture_key": "first",
            "architecture_paths": [requested_path],
        }, "first")
        second = _make_point({
            "text": "second relation",
            "path": "__analysis_architecture__/magento/second.context",
            "branch": "main",
            "architecture_key": "second",
            "architecture_paths": [requested_path],
        }, "second")
        svc.qdrant_client.scroll.side_effect = [
            ([first], "next-page"),
            ([second], None),
        ]
        chunks = []

        stats = svc._query_architecture_context(
            "coll", _branch_filter(), [requested_path], 5,
            ["main"], "main", set(), set(), set(), chunks,
            {}, {}, set(),
        )

        assert [chunk["text"] for chunk in chunks] == [
            "first relation",
            "second relation",
        ]
        assert svc.qdrant_client.scroll.call_args_list[1].kwargs["offset"] == "next-page"
        assert stats["packet_candidates"] == 2
        assert stats["truncated"] is False

    def test_stale_branch_packet_is_rejected_when_pr_changed_its_source(self):
        svc = _build_service()
        packet = _make_point({
            "text": "old effective relation",
            "path": "__analysis_architecture__/magento/packet.context",
            "branch": "main",
            "architecture_key": "global:preference:CartInterface",
            "architecture_paths": ["app/code/Acme/Checkout/etc/di.xml"],
        }, "architecture")
        svc.qdrant_client.scroll.return_value = ([packet], None)
        all_chunks = []

        svc._query_architecture_context(
            "coll", _branch_filter(),
            ["app/code/Acme/Checkout/etc/di.xml"], 10,
            ["main"], "main", set(),
            {"app/code/Acme/Checkout/etc/di.xml"}, set(), all_chunks,
            {}, {}, set(),
        )

        assert all_chunks == []

    def test_pr_packet_replaces_stale_branch_packet(self):
        svc = _build_service()
        branch_packet = _make_point({
            "text": "old effective relation",
            "path": "__analysis_architecture__/magento/packet.context",
            "branch": "main",
            "architecture_key": "global:preference:CartInterface",
            "architecture_paths": ["app/code/Acme/Checkout/etc/di.xml"],
        }, "branch")
        pr_packet = _make_point({
            "text": "new effective relation",
            "path": "__analysis_architecture__/magento/packet.context",
            "branch": "feature",
            "pr": True,
            "pr_number": 42,
            "architecture_key": "global:preference:CartInterface",
            "architecture_paths": ["app/code/Acme/Checkout/etc/di.xml"],
        }, "pr")
        svc.qdrant_client.scroll.return_value = ([branch_packet, pr_packet], None)
        all_chunks = []

        svc._query_architecture_context(
            "coll", _branch_filter(),
            ["app/code/Acme/Checkout/etc/di.xml"], 10,
            ["feature", "main"], "feature", set(),
            {"app/code/Acme/Checkout/etc/di.xml"}, set(), all_chunks,
            {}, {}, set(),
        )

        assert [chunk["text"] for chunk in all_chunks] == ["new effective relation"]


# ─────────────────────────────────────────────────────────────
# _query_definitions (Step 2)
# ─────────────────────────────────────────────────────────────
class TestQueryDefinitions:

    def test_finds_definitions_by_primary_name(self):
        svc = _build_service()

        payload = {
            "text": "class Helper {}",
            "path": "src/Helper.java",
            "branch": "main",
            "primary_name": "Helper",
        }
        pt = _make_point(payload, "d1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        related_defs = {}

        svc._query_definitions(
            "coll", _branch_filter(), {"Helper"},
            ["main"], "main", set(),
            set(), set(), all_chunks, related_defs
        )

        assert "Helper" in related_defs
        assert len(related_defs["Helper"]) == 1
        assert related_defs["Helper"][0]["_match_type"] == "definition"

    def test_skips_changed_files(self):
        svc = _build_service()

        payload = {
            "text": "class Foo {}",
            "path": "src/Foo.java",
            "branch": "main",
            "primary_name": "Foo",
        }
        pt = _make_point(payload, "d2")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        related_defs = {}

        svc._query_definitions(
            "coll", _branch_filter(), {"Foo"},
            ["main"], "main", set(),
            {"src/Foo.java"},  # already a changed file
            set(), all_chunks, related_defs
        )

        assert len(related_defs) == 0

    def test_handles_exception(self):
        svc = _build_service()
        svc.qdrant_client.scroll.side_effect = Exception("network error")

        all_chunks = []
        related_defs = {}

        with pytest.raises(Exception, match="network error"):
            svc._query_definitions(
                "coll", _branch_filter(), {"Foo"},
                ["main"], "main", set(),
                set(), set(), all_chunks, related_defs
            )
        assert len(related_defs) == 0


# ─────────────────────────────────────────────────────────────
# _query_transitive_parents (Step 2b)
# ─────────────────────────────────────────────────────────────
class TestQueryTransitiveParents:

    def test_finds_transitive_parents(self):
        svc = _build_service()

        payload = {
            "text": "class GrandParent {}",
            "path": "src/GrandParent.java",
            "branch": "main",
            "primary_name": "GrandParent",
        }
        pt = _make_point(payload, "tp1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        related_defs = {}

        svc._query_transitive_parents(
            "coll", _branch_filter(), {"GrandParent"},
            ["main"], "main", set(),
            set(), set(), all_chunks, related_defs
        )

        assert "GrandParent" in related_defs
        assert related_defs["GrandParent"][0]["_match_type"] == "transitive_parent"

    def test_skips_changed_file_paths(self):
        svc = _build_service()

        payload = {
            "text": "class P {}",
            "path": "src/P.java",
            "branch": "main",
            "primary_name": "P",
        }
        pt = _make_point(payload, "tp2")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        related_defs = {}

        svc._query_transitive_parents(
            "coll", _branch_filter(), {"P"},
            ["main"], "main", set(),
            {"src/P.java"}, set(), all_chunks, related_defs
        )
        assert len(related_defs) == 0

    def test_handles_exception(self):
        svc = _build_service()
        svc.qdrant_client.scroll.side_effect = Exception("timeout")

        with pytest.raises(Exception, match="timeout"):
            svc._query_transitive_parents(
                "coll", _branch_filter(), {"X"},
                ["main"], "main", set(),
                set(), set(), [], {}
            )


# ─────────────────────────────────────────────────────────────
# _query_class_context (Step 3)
# ─────────────────────────────────────────────────────────────
class TestQueryClassContext:

    def test_finds_class_context(self):
        svc = _build_service()

        payload = {
            "text": "public void otherMethod() {}",
            "path": "src/MyClass.java",
            "branch": "main",
            "parent_class": "MyClass",
        }
        pt = _make_point(payload, "cc1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        class_ctx = {}

        svc._query_class_context(
            "coll", _branch_filter(), {"MyClass"},
            ["main"], "main", set(),
            set(), set(), all_chunks, class_ctx
        )

        assert "MyClass" in class_ctx
        assert class_ctx["MyClass"][0]["_match_type"] == "class_context"

    def test_skips_changed_files(self):
        svc = _build_service()

        payload = {
            "text": "void m() {}",
            "path": "src/Changed.java",
            "branch": "main",
            "parent_class": "Changed",
        }
        pt = _make_point(payload, "cc2")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        class_ctx = {}
        svc._query_class_context(
            "coll", _branch_filter(), {"Changed"},
            ["main"], "main", set(),
            {"src/Changed.java"}, set(), [], class_ctx
        )
        assert len(class_ctx) == 0

    def test_handles_exception(self):
        svc = _build_service()
        svc.qdrant_client.scroll.side_effect = Exception("err")

        with pytest.raises(Exception, match="err"):
            svc._query_class_context(
                "coll", _branch_filter(), {"X"},
                ["main"], "main", set(),
                set(), set(), [], {}
            )


# ─────────────────────────────────────────────────────────────
# _query_namespace_context (Step 4)
# ─────────────────────────────────────────────────────────────
class TestQueryNamespaceContext:

    def test_finds_namespace_context(self):
        svc = _build_service()

        payload = {
            "text": "class Related {}",
            "path": "src/Related.java",
            "branch": "main",
            "namespace": "com.example",
        }
        pt = _make_point(payload, "ns1")
        svc.qdrant_client.scroll.return_value = ([pt], None)

        all_chunks = []
        ns_ctx = {}

        svc._query_namespace_context(
            "coll", _branch_filter(), {"com.example"},
            ["main"], "main", set(),
            set(), set(), all_chunks, ns_ctx
        )

        assert "com.example" in ns_ctx
        assert ns_ctx["com.example"][0]["_match_type"] == "namespace_context"

    def test_handles_exception(self):
        svc = _build_service()
        svc.qdrant_client.scroll.side_effect = Exception("err")

        with pytest.raises(Exception, match="err"):
            svc._query_namespace_context(
                "coll", _branch_filter(), {"ns"},
                ["main"], "main", set(),
                set(), set(), [], {}
            )


# ─────────────────────────────────────────────────────────────
# get_deterministic_context (full orchestration)
# ─────────────────────────────────────────────────────────────
class TestGetDeterministicContext:

    def test_collection_not_found(self):
        svc = _build_service()
        svc.qdrant_client.get_collections.return_value.collections = []
        svc.qdrant_client.get_aliases.return_value.aliases = []

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/Foo.java"],
        )
        assert result["_metadata"]["error"] == "collection_not_found"
        assert result["_metadata"]["retrieval_state"] == "unavailable"
        assert result["chunks"] == []

    def test_full_flow_single_branch(self):
        svc = _build_service()

        # Collection exists
        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        # Step 1: changed file query returns a point with metadata
        changed_pt = _make_point({
            "text": "class Foo extends Bar { void doStuff() {} }",
            "path": "src/Foo.java",
            "branch": "main",
            "parent_class": "Bar",
            "namespace": "com.app",
            "imports": ["com.util.Helper"],
            "extends": ["Bar"],
            "semantic_names": ["Foo"],
            "primary_name": "Foo",
        }, "c1")

        # Step 2: definition lookup returns Bar
        def_pt = _make_point({
            "text": "abstract class Bar {}",
            "path": "src/Bar.java",
            "branch": "main",
            "primary_name": "Bar",
        }, "d1")

        # Step 3: class context returns sibling
        class_pt = _make_point({
            "text": "void sibling() {}",
            "path": "src/BarImpl.java",
            "branch": "main",
            "parent_class": "Bar",
        }, "cl1")

        # Step 4: namespace context
        ns_pt = _make_point({
            "text": "class Config {}",
            "path": "src/Config.java",
            "branch": "main",
            "namespace": "com.app",
        }, "ns1")

        # Scroll calls: changed file (exact match), definitions, class, namespace
        svc.qdrant_client.scroll.side_effect = [
            ([changed_pt], None),   # Step 1: exact path match
            ([def_pt], None),       # Step 2: definitions
            ([], None),             # Step 2b: transitive parents (empty)
            ([class_pt], None),     # Step 3: class context
            ([ns_pt], None),        # Step 4: namespace context
        ]

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/Foo.java"],
        )

        assert len(result["chunks"]) >= 1
        assert "src/Foo.java" in result["changed_files"]
        assert result["_metadata"]["branches_searched"] == ["main"]
        assert result["_metadata"]["retrieval_state"] == "complete"

    def test_with_pr_number(self):
        svc = _build_service()

        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        pt = _make_point({
            "text": "pr code",
            "path": "src/Pr.java",
            "branch": "feat",
            "pr": True,
            "pr_number": 42,
        }, "pr1")

        svc.qdrant_client.scroll.return_value = ([pt], None)

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["feat", "main"],
            file_paths=["src/Pr.java"],
            pr_number=42,
        )
        assert len(result["chunks"]) >= 1

    def test_additional_identifiers_injection(self):
        svc = _build_service()

        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        svc.qdrant_client.scroll.return_value = ([], None)

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/X.java"],
            additional_identifiers=["ExtraType", "HelperFunc", "x"],
        )
        # "x" is only 1 char, should be filtered out; other 2 should be in metadata
        ids_extracted = result["_metadata"]["identifiers_extracted"]
        assert "ExtraType" in ids_extracted
        assert "HelperFunc" in ids_extracted

    def test_query_error_does_not_break_flow(self):
        svc = _build_service()

        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        svc.qdrant_client.scroll.side_effect = Exception("scroll error")

        result = svc.get_deterministic_context(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/Err.java"],
        )
        assert result["chunks"] == []
        assert result["_metadata"]["retrieval_state"] == "failed"
        assert result["_metadata"]["file_status"] == {"src/Err.java": "error"}
        assert result["_metadata"]["failures"][0]["stage"] == "changed_file"

    def test_architecture_safety_cap_marks_context_partial(self):
        svc = _build_service()
        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []
        changed = _make_point({
            "text": "class Checkout {}",
            "path": "src/Checkout.php",
            "branch": "main",
        }, "changed")
        svc.qdrant_client.scroll.return_value = ([changed], None)

        with patch.object(
            svc,
            "_query_architecture_context",
            return_value={"truncated": True, "packet_candidates": 5000},
        ):
            result = svc.get_deterministic_context(
                workspace="ws",
                project="proj",
                branches=["main"],
                file_paths=["src/Checkout.php"],
            )

        assert result["_metadata"]["retrieval_state"] == "partial"
        assert result["_metadata"]["architecture_retrieval"]["truncated"] is True
        assert any(
            failure["error_type"] == "ResultLimit"
            for failure in result["_metadata"]["failures"]
        )
