from __future__ import annotations

import sys
from pathlib import Path

from codecrow_plugins import (
    CandidateClaim,
    FileArtifact,
    GraphFact,
    PluginCatalog,
    PluginRuntime,
    ProjectSelector,
    RepositoryFacts,
    ValidationDecision,
)


PLUGINS_ROOT = Path(__file__).resolve().parents[3]


def _runtime():
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="0123456789abcdef",
        paths=("src/Card.jsx", "src/Product.jsx"),
    ))
    assert capabilities.repository_plugins == ("javascript",)
    assert runtime.repository_analysis_plugins(capabilities) == ("javascript",)
    return runtime, capabilities


def _card() -> FileArtifact:
    return FileArtifact(
        "src/Card.jsx",
        """
import PropTypes from "prop-types";
export default function Card({ title, amount }) {
  return <div>{title}{amount}</div>;
}
Card.propTypes = {
  title: PropTypes.string.isRequired,
  amount: PropTypes.number
};
""".strip(),
    )


def _product(props: str = "title={name} amount={price}") -> FileArtifact:
    return FileArtifact(
        "src/Product.jsx",
        f"""
import Card from "./Card";
export function Product({{ name, price, rest }}) {{
  return <Card {props} />;
}}
""".strip(),
    )


def _analyze(*artifacts: FileArtifact):
    runtime, capabilities = _runtime()
    handle = runtime.start_repository_analysis(
        capabilities,
        "0123456789abcdef",
    )
    handle.ingest(tuple(sorted(artifacts, key=lambda item: item.path)))
    analysis, diagnostics = handle.finish()
    assert diagnostics == ()
    return runtime, capabilities, analysis


def test_exact_relative_import_builds_component_and_prop_contracts():
    _, _, analysis = _analyze(_card(), _product())

    assert len(analysis.packets) == 1
    packet = analysis.packets[0]
    assert packet.paths == ("src/Card.jsx", "src/Product.jsx")
    assert {
        (fact.kind, fact.relation, fact.target)
        for fact in packet.facts
    } == {
        (
            "javascript-component-resolution",
            "resolves-to",
            "src/Card.jsx::Card",
        ),
        (
            "javascript-jsx-prop-contract",
            "passes-declared-prop",
            "src/Card.jsx::Card::amount",
        ),
        (
            "javascript-jsx-prop-contract",
            "passes-required-prop",
            "src/Card.jsx::Card::title",
        ),
    }
    assert len(analysis.snapshots) == 1
    assert analysis.snapshots[0].kind == "javascript-components"


def test_required_prop_omission_is_exact_only_without_a_spread():
    _, _, missing = _analyze(_card(), _product("amount={price}"))
    _, _, spread = _analyze(_card(), _product("amount={price} {...rest}"))

    missing_facts = {
        (fact.kind, fact.target)
        for packet in missing.packets
        for fact in packet.facts
    }
    spread_facts = {
        (fact.kind, fact.target)
        for packet in spread.packets
        for fact in packet.facts
    }

    assert (
        "javascript-jsx-required-prop-missing",
        "src/Card.jsx::Card::title",
    ) in missing_facts
    assert not any(
        kind == "javascript-jsx-required-prop-missing"
        for kind, _ in spread_facts
    )


def test_memoized_component_and_defaulted_destructured_prop_are_indexed():
    component = FileArtifact(
        "src/Card.jsx",
        """
import { memo } from "react";
const Card = memo(({ title = "Untitled", amount, ...rest }) => (
  <div {...rest}>{title}{amount}</div>
));
export default memo(Card);
""".strip(),
    )
    _, _, analysis = _analyze(
        component,
        _product(),
    )

    facts = {
        (fact.kind, fact.target)
        for packet in analysis.packets
        for fact in packet.facts
    }
    assert (
        "javascript-component-resolution",
        "src/Card.jsx::Card",
    ) in facts
    assert (
        "javascript-jsx-prop-contract",
        "src/Card.jsx::Card::title",
    ) in facts
    assert (
        "javascript-jsx-prop-contract",
        "src/Card.jsx::Card::amount",
    ) in facts


def test_named_component_resolution_requires_an_exact_export_binding():
    unexported = FileArtifact(
        "src/Card.jsx",
        """
function Card({ title }) {
  return <div>{title}</div>;
}
""".strip(),
    )
    caller = FileArtifact(
        "src/Product.jsx",
        """
import { Card } from "./Card";
export function Product({ name }) {
  return <Card title={name} />;
}
""".strip(),
    )
    _, _, analysis = _analyze(unexported, caller)

    assert analysis.packets == ()


def test_named_export_alias_and_namespace_usage_resolve_to_local_component():
    component = FileArtifact(
        "src/Card.jsx",
        """
function Card({ title }) {
  return <div>{title}</div>;
}
export { Card as Tile };
""".strip(),
    )
    caller = FileArtifact(
        "src/Product.jsx",
        """
import * as UI from "./Card";
export function Product({ name }) {
  return <UI.Tile title={name} />;
}
""".strip(),
    )
    _, _, analysis = _analyze(component, caller)

    assert any(
        fact.kind == "javascript-component-resolution"
        and fact.target == "src/Card.jsx::Card"
        for packet in analysis.packets
        for fact in packet.facts
    )


def test_dotted_usage_and_dynamic_default_wrapper_remain_unresolved():
    component = FileArtifact(
        "src/Card.jsx",
        """
function Card({ title }) {
  return <div>{title}</div>;
}
export default connect(Card);
""".strip(),
    )
    local_dotted = FileArtifact(
        "src/Product.jsx",
        """
function Card({ title }) {
  return <div>{title}</div>;
}
export function Product({ name }) {
  return <UI.Card title={name} />;
}
""".strip(),
    )
    imported_dynamic = FileArtifact(
        "src/Checkout.jsx",
        """
import Card from "./Card";
export function Checkout({ name }) {
  return <Card title={name} />;
}
""".strip(),
    )
    _, _, analysis = _analyze(
        component,
        local_dotted,
        imported_dynamic,
    )

    assert analysis.packets == ()


def test_snapshot_restore_recomputes_changed_usage_against_unchanged_component():
    runtime, capabilities, baseline = _analyze(_card(), _product())
    handle = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        baseline.snapshots,
    )
    handle.ingest((_product("amount={price}"),))
    updated, diagnostics = handle.finish()

    assert diagnostics == ()
    assert any(
        fact.kind == "javascript-jsx-required-prop-missing"
        and fact.target.endswith("::Card::title")
        for packet in updated.packets
        for fact in packet.facts
    )


def test_deleted_component_removes_cross_file_resolution_after_restore():
    runtime, capabilities, baseline = _analyze(_card(), _product())
    handle = runtime.start_repository_analysis(
        capabilities,
        "fedcba9876543210",
        baseline.snapshots,
    )
    handle.ingest((FileArtifact("src/Card.jsx", "", deleted=True),))
    updated, diagnostics = handle.finish()

    assert diagnostics == ()
    assert updated.packets == ()


def test_javascript_validator_requires_exact_kind_path_and_identifier():
    runtime, capabilities = _runtime()
    presence_fact = GraphFact(
        "javascript-jsx-prop-contract",
        "src/Product.jsx::Product::Card",
        "passes-required-prop",
        "src/Card.jsx::Card::title",
        "src/Product.jsx",
        3,
        related_paths=("src/Card.jsx",),
    )
    defect_fact = GraphFact(
        "javascript-jsx-required-prop-missing",
        "src/Product.jsx::Product::Card",
        "omits-required-prop",
        "src/Card.jsx::Card::title",
        "src/Product.jsx",
        3,
        related_paths=("src/Card.jsx",),
    )
    base = {
        "category": "bug-risk",
        "path": "src/Product.jsx",
        "line": 3,
    }

    presence_only = runtime.validate(
        CandidateClaim(
            **base,
            message="Product passes the required Card title prop with an incompatible value.",
            evidence=(presence_fact,),
            claim_kind="javascript-jsx-prop-contract",
        ),
        capabilities,
    )
    supported = runtime.validate(
        CandidateClaim(
            **base,
            message="Product omits the required Card title prop.",
            evidence=(defect_fact,),
            claim_kind="javascript-jsx-required-prop-missing",
        ),
        capabilities,
    )
    contradicted = runtime.validate(
        CandidateClaim(
            **base,
            message="Product does not pass the required Card title prop.",
            evidence=(presence_fact,),
            claim_kind="javascript-jsx-prop-contract",
        ),
        capabilities,
    )
    wrong_kind = runtime.validate(
        CandidateClaim(
            **base,
            message="Product imports Card.",
            evidence=(presence_fact,),
            claim_kind="javascript-import",
        ),
        capabilities,
    )
    unrelated = runtime.validate(
        CandidateClaim(
            **base,
            message="Checkout passes an unrelated currency prop.",
            evidence=(presence_fact,),
            claim_kind="javascript-jsx-prop-contract",
        ),
        capabilities,
    )
    coarse = runtime.validate(
        CandidateClaim(
            **base,
            message="Card may have a contract problem.",
            evidence=(presence_fact,),
            claim_kind="javascript-file",
        ),
        capabilities,
    )

    assert presence_only[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert presence_only[0].code == "javascript-presence-is-not-defect-proof"
    assert supported[0].decision is ValidationDecision.PASS
    assert supported[0].code == "javascript-exact-defect-proof-present"
    assert contradicted[0].decision is ValidationDecision.REJECT
    assert wrong_kind[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert unrelated[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert coarse[0].decision is ValidationDecision.INSUFFICIENT_EVIDENCE
    assert coarse[0].code == "javascript-coarse-evidence-class"


def test_component_contract_reaches_prompt_visible_exact_evidence():
    _, _, analysis = _analyze(_card(), _product())
    project_root = PLUGINS_ROOT.parent
    sys.path.insert(
        0,
        str(
            project_root
            / "python-ecosystem"
            / "inference-orchestrator"
            / "src"
        ),
    )
    from service.review.orchestrator.context_helpers import format_rag_context

    packet = analysis.packets[0]
    fact_payload = [
        dict(fact.as_metadata())
        for fact in packet.facts
    ]
    text = "\n".join((
        "Deterministic repository architecture context",
        "Plugin: javascript",
        "Kind: javascript-component-relation",
        "Source: src/Product.jsx",
        "Related paths: src/Card.jsx, src/Product.jsx",
        "Facts:",
        *(
            f"- [{fact.kind}] {fact.source} {fact.relation} "
            f"{fact.target} ({fact.path}:{fact.line})"
            for fact in packet.facts
        ),
    ))
    chunks = [
        {
            "text": text,
            "metadata": {
                "path": (
                    "__analysis_architecture__/javascript/"
                    "component-contract.context"
                ),
                "architecture_key": (
                    "javascript-component-relation:src/Product.jsx:0"
                ),
                "architecture_paths": list(packet.paths),
                "plugin_graph_facts": fact_payload,
            },
            "_source": "pr_indexed",
            "_match_type": "architecture_relation",
            "score": 1.0,
        }
    ]
    visible = {}
    prompt = format_rag_context(
        {"relevant_code": chunks},
        pr_changed_files=["src/Product.jsx"],
        visible_evidence_by_id=visible,
    )

    assert "Evidence ID: RAG-" in prompt
    assert "[javascript-component-resolution]" in prompt
    assert "[javascript-jsx-prop-contract]" in prompt
    assert "src/Product.jsx::Product::Card" in prompt
    assert "src/Card.jsx::Card::title" in prompt
    assert {
        fact["kind"]
        for facts in visible.values()
        for fact in facts
    } == {
        "javascript-component-resolution",
        "javascript-jsx-prop-contract",
    }
