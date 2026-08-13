"""
Unit tests for service.review.orchestrator.context_helpers —
extract_symbols_from_diff, extract_diff_snippets, get_diff_snippets_for_batch, format_rag_context.
"""
import pytest
from service.review.orchestrator.context_helpers import (
    extract_symbols_from_diff,
    extract_diff_snippets,
    get_diff_snippets_for_batch,
    format_rag_context,
    rag_evidence_id,
)


SAMPLE_DIFF = """\
diff --git a/src/OrderService.java b/src/OrderService.java
--- a/src/OrderService.java
+++ b/src/OrderService.java
@@ -10,6 +10,10 @@
+    public Order createOrder(CreateOrderRequest request) {
+        OrderValidator validator = new OrderValidator();
+        validator.validate(request);
+        return orderRepository.save(request.toOrder());
+    }
-    public void oldMethod() {
"""


def _prompt_source_facts(values):
    return tuple(
        value for value in values
        if value.get("evidenceType") == "prompt_code_chunk"
    )


def _plugin_graph_facts(values):
    return tuple(
        value for value in values
        if value.get("evidenceType") != "prompt_code_chunk"
    )


# ── extract_symbols_from_diff ────────────────────────────────────

class TestExtractSymbolsFromDiff:

    def test_extracts_camel_case(self):
        symbols = extract_symbols_from_diff(SAMPLE_DIFF)
        assert any("OrderService" in s or "OrderValidator" in s or "CreateOrderRequest" in s for s in symbols)

    def test_extracts_snake_case(self):
        diff = "+    user_name = get_user_name(request)"
        symbols = extract_symbols_from_diff(diff)
        assert any("user_name" in s or "get_user_name" in s for s in symbols)

    def test_preserves_keywords_as_neutral_tokens(self):
        symbols = extract_symbols_from_diff(SAMPLE_DIFF)
        assert "public" in symbols
        assert "return" in symbols

    def test_empty(self):
        assert extract_symbols_from_diff("") == []
        assert extract_symbols_from_diff(None) == []

    def test_limit_20(self):
        # Generate diff with many symbols
        big_diff = "\n".join(f"+    {chr(65+i)}Symbol{i*100}Name = 1" for i in range(26))
        symbols = extract_symbols_from_diff(big_diff)
        assert len(symbols) <= 20


# ── extract_diff_snippets ────────────────────────────────────────

class TestExtractDiffSnippets:

    def test_extracts_added_lines(self):
        snippets = extract_diff_snippets(SAMPLE_DIFF)
        assert len(snippets) > 0
        # Should contain meaningful code from added lines
        combined = " ".join(snippets)
        assert "createOrder" in combined or "OrderValidator" in combined or "orderRepository" in combined

    def test_preserves_comments_and_trivial_added_lines(self):
        diff = "+// comment\n+#\n+{\n+}\n+\n+   real_code = True"
        snippets = extract_diff_snippets(diff)
        combined = "\n".join(snippets)
        assert "// comment" in combined
        assert "#" in combined
        assert "{" in combined
        assert "}" in combined

    def test_empty(self):
        assert extract_diff_snippets("") == []
        assert extract_diff_snippets(None) == []

    def test_limit_10(self):
        big = "\n".join(f"+    statement_{i} = very_long_code_expression_{i}()" for i in range(50))
        snippets = extract_diff_snippets(big)
        assert len(snippets) <= 10


# ── get_diff_snippets_for_batch ──────────────────────────────────

class TestGetDiffSnippetsForBatch:

    def test_returns_all_snippets(self):
        """Since Java snippets are clean code without file paths, all are returned."""
        all_snippets = ["def foo():", "class Bar:", "import os"]
        batch_files = ["src/app.py"]
        result = get_diff_snippets_for_batch(all_snippets, batch_files)
        assert result == all_snippets

    def test_empty(self):
        assert get_diff_snippets_for_batch([], ["a.py"]) == []


# ── format_rag_context ───────────────────────────────────────────

class TestFormatRagContext:

    def test_empty_input(self):
        assert format_rag_context(None) == ""
        assert format_rag_context({}) == ""
        assert format_rag_context({"relevant_code": []}) == ""

    def test_basic_chunk(self):
        rag = {
            "relevant_code": [
                {
                    "text": "def process(): pass",
                    "score": 0.90,
                    "metadata": {"path": "src/proc.py", "content_type": "functions_classes"},
                    "_source": "semantic",
                }
            ]
        }
        result = format_rag_context(rag)
        assert "src/proc.py" in result
        assert "def process(): pass" in result

    def test_retrieved_chunk_has_stable_citation_id(self):
        chunk = {
            "text": "exact relation",
            "metadata": {
                "path": "__analysis_architecture__/relation.context",
                "architecture_key": "relation-key",
            },
            "_match_type": "architecture_relation",
        }

        first = rag_evidence_id(chunk)
        second = rag_evidence_id(dict(chunk))
        result = format_rag_context({"relevant_code": [chunk]})

        assert first == second
        assert first.startswith("RAG-")
        assert f"Evidence ID: {first}" in result

    def test_prompt_visible_semantic_chunk_is_available_as_citation(self):
        chunk = {
            "text": "def process(): pass",
            "score": 0.90,
            "metadata": {"path": "src/proc.py"},
            "_source": "semantic",
        }
        visible = {}

        result = format_rag_context(
            {"relevant_code": [chunk]},
            visible_evidence_by_id=visible,
        )

        evidence_id = rag_evidence_id(chunk)
        assert f"Evidence ID: {evidence_id}" in result
        assert len(_prompt_source_facts(visible[evidence_id])) == 1
        assert _prompt_source_facts(visible[evidence_id])[0]["content"] == chunk["text"]

    def test_prompt_visible_semantic_graph_fact_is_available_to_validation(self):
        fact = {
            "kind": "java-type",
            "source": "com.example.App",
            "relation": "declares",
            "target": "App",
            "path": "src/App.java",
            "line": 1,
        }
        chunk = {
            "text": (
                "[java-type] com.example.App declares App\n"
                "public class App {}"
            ),
            "score": 0.95,
            "metadata": {
                "path": "src/App.java",
                "plugin_graph_facts": [fact],
            },
            "_source": "semantic",
        }
        visible = {}

        result = format_rag_context(
            {"relevant_code": [chunk]},
            visible_evidence_by_id=visible,
        )

        assert "[java-type]" in result
        assert _plugin_graph_facts(visible[rag_evidence_id(chunk)]) == (fact,)
        assert len(_prompt_source_facts(visible[rag_evidence_id(chunk)])) == 1

    def test_repeated_file_fact_prefix_is_rendered_once_without_losing_source(self):
        fact = {
            "kind": "java-type",
            "source": "com.example.App",
            "relation": "declares",
            "target": "App",
            "path": "src/App.java",
            "line": 1,
        }
        fact_line = "[java-type] com.example.App declares App"
        first = {
            "text": (
                "Plugin graph facts:\n"
                f"{fact_line}\n\n"
                "public class App {"
            ),
            "score": 0.95,
            "metadata": {
                "path": "src/App.java",
                "plugin_graph_facts": [fact],
            },
            "_match_type": "definition",
            "_source": "deterministic",
        }
        second = {
            "text": (
                "Plugin graph facts:\n"
                f"{fact_line}\n\n"
                "void execute() {}"
            ),
            "score": 0.94,
            "metadata": {
                "path": "src/App.java",
                "plugin_graph_facts": [fact],
            },
            "_match_type": "definition",
            "_source": "deterministic",
        }
        visible = {}

        result = format_rag_context(
            {"relevant_code": [first, second]},
            visible_evidence_by_id=visible,
        )

        assert result.count(fact_line) == 1
        assert "public class App {" in result
        assert "void execute() {}" in result
        assert _plugin_graph_facts(visible[rag_evidence_id(first)]) == (fact,)
        assert _plugin_graph_facts(visible[rag_evidence_id(second)]) == ()
        assert all(
            len(_prompt_source_facts(visible[evidence_id])) == 1
            for evidence_id in (rag_evidence_id(first), rag_evidence_id(second))
        )

    def test_metadata_facts_are_rendered_once_without_mutating_stored_source(self):
        fact = {
            "kind": "python-call",
            "source": "service.review",
            "relation": "calls",
            "target": "validate",
            "path": "src/service.py",
            "line": 10,
        }
        fact_line = "[python-call] service.review calls validate"
        first = {
            "text": "def review():\n    validate()",
            "score": 0.95,
            "metadata": {
                "path": "src/service.py",
                "plugin_graph_facts": [fact],
            },
            "_match_type": "changed_file",
            "_source": "deterministic",
        }
        second = {
            "text": "def validate():\n    return True",
            "score": 0.94,
            "metadata": {
                "path": "src/service.py",
                "plugin_graph_facts": [fact],
            },
            "_match_type": "definition",
            "_source": "deterministic",
        }
        visible = {}

        result = format_rag_context(
            {"relevant_code": [first, second]},
            visible_evidence_by_id=visible,
        )

        assert result.count(fact_line) == 1
        assert "def review()" in result
        assert "def validate()" in result
        assert _plugin_graph_facts(visible[rag_evidence_id(first)]) == ()
        assert _plugin_graph_facts(visible[rag_evidence_id(second)]) == (fact,)
        assert all(
            len(_prompt_source_facts(visible[evidence_id])) == 1
            for evidence_id in (rag_evidence_id(first), rag_evidence_id(second))
        )

    def test_omitted_fact_prefix_does_not_hide_later_visible_copy(self):
        fact = {
            "kind": "java-type",
            "source": "com.example.App",
            "relation": "declares",
            "target": "App",
            "path": "src/App.java",
            "line": 1,
        }
        fact_line = "[java-type] com.example.App declares App"
        oversized = {
            "text": (
                "Plugin graph facts:\n"
                + ("[java-call] com.example.App calls helper\n" * 30)
                + f"{fact_line}\n\n"
                + ("x" * 1_000)
            ),
            "score": 0.95,
            "metadata": {
                "path": "src/App.java",
                "plugin_graph_facts": [fact],
            },
            "_match_type": "definition",
            "_source": "deterministic",
        }
        later = {
            "text": (
                "Plugin graph facts:\n"
                f"{fact_line}\n\n"
                "public class App {}"
            ),
            "score": 0.94,
            "metadata": {
                "path": "src/App.java",
                "plugin_graph_facts": [fact],
            },
            "_match_type": "definition",
            "_source": "deterministic",
        }
        visible = {}

        result = format_rag_context(
            {"relevant_code": [oversized, later]},
            max_chars=2_400,
            max_chunk_chars=1_300,
            visible_evidence_by_id=visible,
        )

        assert fact_line in result
        assert _plugin_graph_facts(visible[rag_evidence_id(later)]) == (fact,)

    def test_metadata_fact_hidden_by_chunk_truncation_cannot_validate(self):
        visible_fact = {
            "kind": "magento-effective-route",
            "source": "checkout",
            "relation": "handled-by-module",
            "target": "Acme_Checkout",
            "path": "app/code/Acme/Checkout/etc/frontend/routes.xml",
            "line": 1,
        }
        hidden_fact = {
            "kind": "magento-webapi-route",
            "source": "POST /V1/cart",
            "relation": "invokes",
            "target": "Acme\\Api\\CartInterface::save",
            "path": "app/code/Acme/Checkout/etc/webapi.xml",
            "line": 1,
        }
        chunk = {
            "text": (
                "[magento-effective-route] checkout handled-by-module "
                "Acme_Checkout\n"
                + ("x" * 2_000)
                + "\n[magento-webapi-route] POST /V1/cart invokes "
                "Acme\\Api\\CartInterface::save"
            ),
            "score": 1.0,
            "metadata": {
                "path": "__analysis_architecture__/magento/routes.context",
                "plugin_graph_facts": [visible_fact, hidden_fact],
            },
            "_match_type": "architecture_relation",
        }
        visible = {}

        format_rag_context(
            {"relevant_code": [chunk]},
            max_chunk_chars=512,
            visible_evidence_by_id=visible,
        )

        assert _plugin_graph_facts(visible[rag_evidence_id(chunk)]) == (visible_fact,)

    def test_filters_deleted_files(self):
        rag = {
            "relevant_code": [
                {
                    "text": "old code",
                    "score": 0.90,
                    "metadata": {"path": "deleted.py"},
                    "_source": "semantic",
                },
                {
                    "text": "kept code",
                    "score": 0.90,
                    "metadata": {"path": "kept.py"},
                    "_source": "semantic",
                },
            ]
        }
        result = format_rag_context(rag, deleted_files=["deleted.py"])
        assert "deleted.py" not in result
        assert "kept.py" in result

    def test_tiered_budgeting(self):
        """Tier 1 (definition) chunks should appear in output."""
        chunks = [
            {
                "text": f"class Base{i}: pass",
                "score": 0.95,
                "metadata": {"path": f"src/base{i}.py", "content_type": "functions_classes"},
                "_match_type": "definition",
                "_source": "deterministic",
            }
            for i in range(12)
        ]
        rag = {"relevant_code": chunks}
        result = format_rag_context(rag)
        count = sum(1 for i in range(12) if f"src/base{i}.py" in result)
        assert count == 12

    def test_focused_architecture_relations_are_not_cut_at_eight(self):
        rag = {
            "relevant_code": [
                {
                    "text": f"[graph-fact] Source{index} resolves-to Target{index}",
                    "score": 0.95,
                    "metadata": {
                        "path": f"__analysis_architecture__/packet-{index}.context",
                        "architecture_kind": f"kind-{index % 4}",
                    },
                    "_match_type": "architecture_relation",
                    "_source": "pr_indexed",
                }
                for index in range(20)
            ],
        }

        result = format_rag_context(rag)

        assert sum(
            f"Source{index} resolves-to Target{index}" in result
            for index in range(20)
        ) == 20
        assert len(result) <= 32_000

    def test_exact_architecture_relations_are_not_silently_cut_at_sixty_four(self):
        rag = {
            "relevant_code": [
                {
                    "text": (
                        f"[graph-fact] Source{index} resolves-to Target{index}"
                    ),
                    "score": 0.95,
                    "metadata": {
                        "path": (
                            "__analysis_architecture__/"
                            f"packet-{index}.context"
                        ),
                        "architecture_kind": f"kind-{index % 5}",
                    },
                    "_match_type": "architecture_relation",
                    "_source": "pr_indexed",
                }
                for index in range(65)
            ],
        }

        result = format_rag_context(rag)

        assert "Source64 resolves-to Target64" in result
        assert sum(
            f"Source{index} resolves-to Target{index}" in result
            for index in range(65)
        ) == 65
        assert len(result) <= 32_000

    def test_character_budget_keeps_structural_context_first(self):
        rag = {
            "relevant_code": [
                {
                    "text": "class RequiredBase:\n" + ("x = 1\n" * 500),
                    "score": 1.0,
                    "metadata": {"path": "src/RequiredBase.py"},
                    "_match_type": "definition",
                    "_source": "deterministic",
                },
                {
                    "text": "def merely_similar():\n" + ("return 1\n" * 500),
                    "score": 0.99,
                    "metadata": {"path": "src/Similar.py"},
                    "_source": "semantic",
                },
            ]
        }

        result = format_rag_context(
            rag,
            max_chars=1_600,
            max_chunk_chars=1_200,
        )

        assert len(result) <= 1_600
        assert "src/RequiredBase.py" in result
        assert "src/Similar.py" not in result
        assert "Context chunk truncated by deterministic prompt budget" in result
        assert result.count("```") == 2

    def test_character_budget_caps_multiple_large_chunks(self):
        rag = {
            "relevant_code": [
                {
                    "text": f"class Base{index}:\n" + (f"value_{index} = 1\n" * 600),
                    "score": 1.0,
                    "metadata": {"path": f"src/Base{index}.py"},
                    "_match_type": "definition",
                    "_source": "deterministic",
                }
                for index in range(8)
            ]
        }

        result = format_rag_context(
            rag,
            max_chars=5_000,
            max_chunk_chars=2_000,
        )

        assert len(result) <= 5_000
        assert "src/Base0.py" in result
        assert "src/Base7.py" not in result
        assert result.count("```") % 2 == 0

    def test_complete_current_file_chunk_is_removed_but_related_file_remains(self):
        rag = {
            "relevant_code": [
                {
                    "text": "class Reviewed:\n    pass",
                    "score": 1.0,
                    "metadata": {"path": "src/Reviewed.py"},
                    "_match_type": "changed_file",
                    "_source": "pr_indexed",
                },
                {
                    "text": "class Dependency:\n    pass",
                    "score": 0.95,
                    "metadata": {"path": "src/Dependency.py"},
                    "_match_type": "definition",
                    "_source": "deterministic",
                },
            ]
        }

        result = format_rag_context(
            rag,
            current_file_complete_paths={"src/Reviewed.py"},
        )

        assert "src/Reviewed.py" not in result
        assert "src/Dependency.py" in result

    def test_complete_current_file_keeps_exact_architecture_evidence(self):
        fact = {
            "kind": "python-call",
            "source": "src.reviewed",
            "relation": "calls",
            "target": "dependency",
            "path": "src/Reviewed.py",
            "related_paths": ["src/Dependency.py"],
            "attributes": {},
        }
        rag = {
            "relevant_code": [
                {
                    "text": (
                        "[python-call] src.reviewed calls dependency\n"
                        "implementation details"
                    ),
                    "score": 1.0,
                    "metadata": {
                        "path": "src/Reviewed.py",
                        "architecture_key": "python-file:src/Reviewed.py",
                        "plugin_graph_facts": [fact],
                    },
                    "_match_type": "architecture_relation",
                    "_source": "pr_indexed",
                },
            ]
        }
        visible = {}

        result = format_rag_context(
            rag,
            current_file_complete_paths={"src/Reviewed.py"},
            visible_evidence_by_id=visible,
        )

        assert "src/Reviewed.py" in result
        assert "[python-call] src.reviewed calls dependency" in result
        assert tuple(_plugin_graph_facts(value) for value in visible.values()) == ((fact,),)

    def test_truncated_current_file_chunk_is_retained(self):
        rag = {
            "relevant_code": [
                {
                    "text": "middle_of_large_file()",
                    "score": 1.0,
                    "metadata": {"path": "src/Large.py"},
                    "_match_type": "changed_file",
                    "_source": "pr_indexed",
                },
            ]
        }

        result = format_rag_context(
            rag,
            current_file_complete_paths=set(),
        )

        assert "src/Large.py" in result
        assert "middle_of_large_file()" in result

    def test_low_score_documentation_chunk_preserved(self):
        rag = {
            "relevant_code": [
                {
                    "text": "readme content",
                    "score": 0.50,
                    "metadata": {"path": "README.md", "content_type": "documentation"},
                    "_source": "semantic",
                },
            ]
        }
        result = format_rag_context(rag)
        assert "README.md" in result
        assert "readme content" in result

    def test_deduplication(self):
        """Repeated retrieval of the same full evidence identity is deduplicated."""
        rag = {
            "relevant_code": [
                {
                    "text": "same content here",
                    "score": 0.90,
                    "metadata": {"path": "src/a/util.py"},
                    "_source": "semantic",
                },
                {
                    "text": "same content here",
                    "score": 0.88,
                    "metadata": {"path": "src/a/util.py"},
                    "_source": "semantic",
                },
            ]
        }
        result = format_rag_context(rag)
        assert result.count("same content here") == 1

    def test_same_basename_and_content_in_distinct_paths_are_not_deduplicated(self):
        rag = {
            "relevant_code": [
                {
                    "text": "same content here",
                    "score": 0.90,
                    "metadata": {"path": "src/a/util.py"},
                },
                {
                    "text": "same content here",
                    "score": 0.88,
                    "metadata": {"path": "src/b/util.py"},
                },
            ]
        }

        result = format_rag_context(rag)

        assert "src/a/util.py" in result
        assert "src/b/util.py" in result
        assert result.count("same content here") == 2

    def test_magento_module_di_files_with_identical_prefixes_remain_distinct(self):
        shared_prefix = "<config>" + (" " * 350)
        rag = {
            "relevant_code": [
                {
                    "text": shared_prefix + "<preference for='Cart' type='CartImpl'/></config>",
                    "score": 1.0,
                    "metadata": {
                        "path": "app/code/Acme/Cart/etc/di.xml",
                        "architecture_key": "Acme_Cart:global",
                    },
                    "_match_type": "architecture_relation",
                },
                {
                    "text": shared_prefix + "<type name='Checkout'><plugin name='tax'/></type></config>",
                    "score": 1.0,
                    "metadata": {
                        "path": "app/code/Acme/Checkout/etc/di.xml",
                        "architecture_key": "Acme_Checkout:global",
                    },
                    "_match_type": "architecture_relation",
                },
            ]
        }

        result = format_rag_context(rag)

        assert "app/code/Acme/Cart/etc/di.xml" in result
        assert "app/code/Acme/Checkout/etc/di.xml" in result
        assert "CartImpl" in result
        assert "name='tax'" in result

    def test_stale_chunk_from_modified_file_low_score(self):
        rag = {
            "relevant_code": [
                {
                    "text": "stale code",
                    "score": 0.50,
                    "metadata": {"path": "modified.py"},
                    "_source": "semantic",
                },
            ]
        }
        result = format_rag_context(rag, pr_changed_files=["modified.py"])
        assert result == ""

    def test_pr_indexed_not_filtered(self):
        """PR-indexed chunks from modified files should NOT be filtered."""
        rag = {
            "relevant_code": [
                {
                    "text": "fresh indexed code",
                    "score": 0.80,
                    "metadata": {"path": "modified.py"},
                    "_source": "pr_indexed",
                },
            ]
        }
        result = format_rag_context(rag, pr_changed_files=["modified.py"])
        assert "fresh indexed code" in result

    def test_base_architecture_packet_touching_modified_source_is_filtered(self):
        rag = {
            "relevant_code": [{
                "text": "old effective DI relation",
                "score": 1.0,
                "metadata": {
                    "path": "__analysis_architecture__/magento/packet.context",
                    "architecture_context": True,
                    "architecture_paths": [
                        "app/code/Acme/Checkout/etc/di.xml",
                        "app/code/Acme/Checkout/Plugin/OldPlugin.php",
                    ],
                },
                "_source": "deterministic",
                "_match_type": "architecture_relation",
            }]
        }

        result = format_rag_context(
            rag,
            pr_changed_files=["app/code/Acme/Checkout/etc/di.xml"],
        )

        assert result == ""

    def test_pr_architecture_packet_touching_modified_source_is_retained(self):
        rag = {
            "relevant_code": [{
                "text": "new DI relation",
                "score": 1.0,
                "metadata": {
                    "path": "__analysis_architecture__/magento/packet.context",
                    "architecture_context": True,
                    "architecture_paths": ["app/code/Acme/Checkout/etc/di.xml"],
                },
                "_source": "pr_indexed",
                "_match_type": "architecture_relation",
            }]
        }

        result = format_rag_context(
            rag,
            pr_changed_files=["app/code/Acme/Checkout/etc/di.xml"],
        )

        assert "new DI relation" in result
