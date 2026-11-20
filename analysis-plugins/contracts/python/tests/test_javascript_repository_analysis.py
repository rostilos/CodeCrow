from __future__ import annotations

import sys
import base64
import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest

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


def _aliases(analysis):
    return {fact.source: fact.target for packet in analysis.packets for fact in packet.facts
            if fact.relation == 'declares-file-alias'}


def test_package_exports_retain_existing_exact_and_wildcard_targets_with_shadowing():
    manifest = FileArtifact('packages/auth/package.json', json.dumps(dict(name='@scope/auth', exports={
        '.': './src/auth.ts', './admin': './src/admin.tsx', './routes/*': './src/routes/*',
        './routes/private/*': None, './routes/login.ts': './src/login.ts',
        './routes/conditional.ts': {'import': './src/routes/conditional.ts'},
        './routes/array.ts': ['./src/routes/array.ts'], './absent': './src/absent.ts',
        './twice/*': './src/*/copy-*.ts',
    })))
    paths = ('src/auth.ts', 'src/admin.tsx', 'src/routes/setup.ts', 'src/routes/login.ts',
             'src/routes/private/secret.ts', 'src/login.ts', 'src/routes/conditional.ts',
             'src/routes/array.ts', 'src/a/copy-a.ts', 'src/b/copy-c.ts')
    runtime, capabilities, analysis = _analyze(manifest, *(FileArtifact('packages/auth/' + p, '') for p in paths))
    expected = {'@scope/auth': 'packages/auth/src/auth.ts', '@scope/auth/admin': 'packages/auth/src/admin.tsx',
                '@scope/auth/routes/setup.ts': 'packages/auth/src/routes/setup.ts',
                '@scope/auth/routes/login.ts': 'packages/auth/src/login.ts', '@scope/auth/twice/a': 'packages/auth/src/a/copy-a.ts'}
    assert _aliases(analysis) == expected
    restored, diagnostics = runtime.start_repository_analysis(capabilities, 'next', analysis.snapshots).finish()
    assert not diagnostics and _aliases(restored) == expected
    for packet in analysis.packets:
        for fact in packet.facts:
            if fact.relation != 'declares-file-alias':
                continue
            assert fact.path == manifest.path and fact.related_paths == (fact.target,)
            assert {fact.path, fact.target} <= set(packet.paths)
            assert dict(fact.attributes)['jsonPointer'].startswith('/exports/')


def test_export_patterns_are_declarations_not_file_inventory_or_runtime_bindings():
    manifest = FileArtifact('packages/auth/package.json', json.dumps(dict(name='@scope/auth', exports={
        './routes/*': './src/routes/*', './empty/*': './missing/*', './conditional/*': {'import': './conditional/*'},
    })))
    runtime, capabilities, analysis = _analyze(manifest, FileArtifact('packages/auth/src/routes/login.ts', ''))
    patterns = [fact for packet in analysis.packets for fact in packet.facts if fact.relation == 'declares-file-pattern']
    assert {fact.target for fact in patterns} == {'packages/auth/src/routes/*', 'packages/auth/missing/*'}
    assert all(fact.source == fact.path == manifest.path and not fact.related_paths for fact in patterns)
    assert all(dict(fact.attributes)['targetSpecifier'].startswith('./') for fact in patterns)
    assert all('*' not in path for packet in analysis.packets for path in packet.paths)
    assert _aliases(analysis) == {'@scope/auth/routes/login.ts': 'packages/auth/src/routes/login.ts'}
    restored, diagnostics = runtime.start_repository_analysis(capabilities, 'next', analysis.snapshots).finish()
    assert not diagnostics and restored.packets == analysis.packets
    handle = runtime.start_repository_analysis(capabilities, 'deleted', analysis.snapshots)
    handle.ingest((FileArtifact(manifest.path, '', deleted=True),))
    deleted, diagnostics = handle.finish()
    assert not diagnostics and not any(f.relation == 'declares-file-pattern' for p in deleted.packets for f in p.facts)


@pytest.mark.parametrize('exports', [None, './dist/missing.js', {'import': './dist/missing.js'}, {'invalid': None, '.': './index.js'}])
def test_package_identity_is_independent_of_exports_and_survives_snapshot_updates(exports):
    path = 'packages/admin/package.json'
    runtime, capabilities, analysis = _analyze(FileArtifact(path, json.dumps(dict(name='@scope/admin', exports=exports))))
    for name in ('@scope/admin', 'renamed-admin', None):
        if name != '@scope/admin':
            handle = runtime.start_repository_analysis(capabilities, 'next', analysis.snapshots)
            handle.ingest((FileArtifact(path, json.dumps(dict(name=name)), deleted=name is None),))
            analysis, diagnostics = handle.finish()
            assert not diagnostics
        restored, diagnostics = runtime.start_repository_analysis(capabilities, 'restored', analysis.snapshots).finish()
        assert not diagnostics and restored.packets == analysis.packets
        facts = [f for p in restored.packets for f in p.facts if f.relation == 'declares-package']
        if name is None:
            assert not facts
        else:
            fact, = facts
            assert (fact.source, fact.target, fact.path) == (name, path, path)
            assert fact.related_paths == (path,) and dict(fact.attributes)['jsonPointer'] == '/name'


@pytest.mark.parametrize('name', [None, 42, {}, '', '../pkg', '@scope/pkg/subpath'])
def test_invalid_package_names_do_not_create_identity_facts(name):
    _, _, analysis = _analyze(FileArtifact('package.json', json.dumps(dict(name=name))))
    assert not [f for p in analysis.packets for f in p.facts if f.relation == 'declares-package']


@pytest.mark.parametrize('exports,expected', [
    ('./src/main.ts', {'pkg': 'src/main.ts'}),
    ({'./*': './src/*', './m*': None}, {'pkg/other.ts': 'src/other.ts'}),
    ({'./*': './src/*', './*.ts': None}, {}),
    ({'./*': './src/*', './main.ts': None}, {'pkg/other.ts': 'src/other.ts'}),
    ({'.': '../src/main.ts'}, {}), ({'.': './src/../src/main.ts'}, {}),
    ({'.': './node_modules/hidden.ts'}, {}), ({'.': './NODE_MODULES/hidden.ts'}, {}),
    ({'.': './src/%6dain.ts'}, {}), ({'.': './src/main.ts?x'}, {}),
    ({'.': './src/main.ts', 'import': './src/main.ts'}, {}),
    ({'./**': './src/*'}, {}), ({'import': './src/main.ts'}, {}),
    ({'./m*ain.ts': './src/m*ain.ts'}, {}),
])
def test_package_export_targets_respect_precedence_and_keep_unsupported_entries_unknown(exports, expected):
    _, _, analysis = _analyze(FileArtifact('package.json', json.dumps(dict(name='pkg', exports=exports))),
        *(FileArtifact(p, '') for p in ('src/main.ts', 'src/other.ts', 'node_modules/hidden.ts', 'NODE_MODULES/hidden.ts')))
    assert _aliases(analysis) == expected


def test_package_alias_inventory_changes_and_deletions_survive_snapshot_restore():
    def manifest(target):
        return FileArtifact('package.json', json.dumps(dict(name='pkg', exports=target)))
    runtime, capabilities, analysis = _analyze(manifest('./a.ts'), FileArtifact('a.ts', ''), FileArtifact('b.ts', ''))
    for updates, expected in [
        ((manifest('./b.ts'),), {'pkg': 'b.ts'}),
        ((FileArtifact('b.ts', '', deleted=True),), {}),
        ((FileArtifact('b.ts', ''),), {'pkg': 'b.ts'}),
        ((FileArtifact('package.json', '', deleted=True),), {}),
    ]:
        handle = runtime.start_repository_analysis(capabilities, 'next', analysis.snapshots)
        handle.ingest(updates)
        analysis, diagnostics = handle.finish()
        assert not diagnostics and _aliases(analysis) == expected


def test_old_component_snapshot_restores_with_observable_missing_alias_inventory(caplog):
    runtime, capabilities, analysis = _analyze(_card(), _product())
    snapshot, = analysis.snapshots
    data = json.loads(gzip.decompress(base64.b64decode(snapshot.content)))
    legacy = replace(snapshot, content=base64.b64encode(gzip.compress(json.dumps(data['components']).encode())).decode())
    restored, diagnostics = runtime.start_repository_analysis(capabilities, 'next', (legacy,)).finish()
    assert not diagnostics and restored.packets == analysis.packets
    assert 'aliases require a repository refresh' in caplog.text


def test_malformed_manifest_removes_old_alias_and_reports_optional_enrichment_failure():
    runtime, capabilities, analysis = _analyze(FileArtifact('package.json', '{"name":"pkg","exports":"./a.ts"}'), FileArtifact('a.ts', ''))
    handle = runtime.start_repository_analysis(capabilities, 'next', analysis.snapshots)
    handle.ingest((FileArtifact('package.json', '{broken'),))
    updated, diagnostics = handle.finish()
    assert diagnostics and not _aliases(updated)
    assert not [f for p in updated.packets for f in p.facts if f.relation == 'declares-package']


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


def test_component_contract_reaches_structural_graph_relation_map(tmp_path):
    _, _, analysis = _analyze(_card(), _product())
    project_root = PLUGINS_ROOT.parent
    sys.path.insert(
        0,
        str(
            project_root
            / "python-ecosystem"
            / "rag-pipeline"
            / "src"
        ),
    )
    from rag_pipeline.core.structural_store import (
        StructuralGenerationStore,
        StructuralGraphReader,
        StructuralGraphWriter,
    )

    packet = analysis.packets[0]
    store = StructuralGenerationStore(tmp_path / "structural-index")
    pending = store.pending_paths("cc_workspace_project_main_generation")
    connection = store.initialize(pending)
    try:
        writer = StructuralGraphWriter(connection)
        for fact in packet.facts:
            writer.add_graph_fact(
                fact,
                plugin_id="javascript",
                packet_kind=packet.kind,
                packet_key=packet.key,
            )
        writer.resolve_relations()
        connection.commit()
        reader = StructuralGraphReader(connection, {
            "branch": "main",
            "repository_revision": "0123456789abcdef",
            "generation_manifest_sha256": "a" * 64,
        })
        relation_map = reader.relations_for_paths(
            ["src/Product.jsx"],
            max_relations=20,
        )
    finally:
        connection.close()

    relations = relation_map["relations"]
    assert relation_map["coverage"]["state"] == "complete"
    assert all(relation["evidenceId"].startswith("relation:") for relation in relations)
    assert all(relation["origin"]["plugin"] == "javascript" for relation in relations)
    assert {
        relation["kind"]
        for relation in relations
    } == {
        "javascript-component-resolution",
        "javascript-jsx-prop-contract",
    }
    assert any(
        relation["source"] == "src/Product.jsx::Product::Card"
        and relation["target"] == "src/Card.jsx::Card::title"
        for relation in relations
    )
