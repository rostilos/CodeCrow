from pathlib import Path

import pytest

from codecrow_plugins import FileArtifact, PluginCatalog


@pytest.fixture(params=[("javascript", "js"), ("typescript", "ts"), ("typescript", "tsx")])
def extract(request):
    language, suffix = request.param
    plugin = PluginCatalog.discover(Path(__file__).resolve().parents[3]).implementation("babel")

    def run(source):
        outcome = plugin.index_file(FileArtifact(f"build.config.{suffix}", source))
        if outcome.status.value == "abstained":
            return []
        assert outcome.status.value == "handled", outcome
        return [fact for fact in outcome.value if fact.relation == "configures-module"]

    return run


@pytest.mark.parametrize("declaration,call", [
    ('import { transformAsync as compile } from "@babel/core";', 'compile(code, OPTIONS)'),
    ('import * as babel from "@babel/core";', 'babel.transformSync(code, OPTIONS)'),
    ('import babel from "@babel/core";', 'babel.transformFileAsync(file, OPTIONS)'),
    ('import { transformFromAstAsync } from "@babel/core";', 'transformFromAstAsync(ast, code, OPTIONS)'),
    ('import react from "@vitejs/plugin-react";', 'react({babel: OPTIONS})'),
])
def test_configured_modules_preserve_literal_origin_and_loader(extract, declaration, call):
    options = '{plugins: ["@lingui/babel-plugin-lingui-macro", ["exact:custom", {}]], presets: ["@babel/preset-env"], parserOpts: {plugins: ["jsx", "typescript"]}}'
    facts = extract(declaration + "\n" + call.replace("OPTIONS", options))
    assert {f.target for f in facts} == {"@lingui/babel-plugin-lingui-macro", "exact:custom", "@babel/preset-env"}
    assert all(f.line == 2 and f.path.startswith("build.config.") for f in facts)
    assert all(dict(f.attributes)["loader"] == ("@vitejs/plugin-react" if "import react" in declaration else "@babel/core") for f in facts)
    assert all(not f.related_paths for f in facts)


@pytest.mark.parametrize("source", [
    'const x = {plugins: ["not-a-module"]};',
    'import react from "unrelated"; react({babel: {plugins: ["not-a-module"]}});',
    'import { transformAsync as compile } from "@babel/core"; function x(compile) { compile(code, {plugins:["shadow"]}); }',
    'import react from "@vitejs/plugin-react"; function x() { const react = other; react({babel:{plugins:["shadow"]}}); }',
    'import react from "@vitejs/plugin-react"; const x = react => react({babel:{plugins:["shadow"]}});',
    'import react from "@vitejs/plugin-react"; try {} catch (react) { react({babel:{plugins:["shadow"]}}); }',
    'import react from "@vitejs/plugin-react"; react = other; react({babel:{plugins:["reassigned"]}});',
    'import { transformAsync } from "@babel/core"; transformAsync(...args, {plugins:["unknown-position"]});',
    'import { transformAsync } from "@babel/core"; transformAsync(code, {plugins:["overridden"], ...other});',
    'import { transformAsync } from "@babel/core"; transformAsync(code, {plugins:["overridden"], plugins:[]});',
    'import { transformAsync } from "@babel/core"; transformAsync(code, {plugins:["overridden"], [key]:[]});',
    'import { transformAsync } from "@babel/core"; transformAsync(code, {plugins: [`dynamic-${name}`, factory(), ["disabled", false], "escaped\\x21"], parserOpts:{plugins:["jsx"]}});',
    'import react from "@vitejs/plugin-react"; react({babel: () => ({plugins:["dynamic"]})});',
    'import react from "@vitejs/plugin-react"; react({plugins:["wrong-options-owner"]});',
])
def test_unsupported_or_unrelated_configuration_does_not_invent_module_evidence(extract, source):
    assert extract(source) == []


@pytest.mark.parametrize("dependency", ["@babel/core", "@vitejs/plugin-react"])
def test_package_detection_allows_source_proven_loaders_in_sibling_packages(dependency):
    from codecrow_plugins import PluginRuntime, ProjectSelector, RepositoryFacts
    catalog = PluginCatalog.discover(Path(__file__).resolve().parents[3])
    paths = ("outside.ts", "packages/admin/build.ts", "packages/admin/package.json")
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision="head", paths=paths,
        marker_contents={"packages/admin/package.json": '{"devDependencies":{"' + dependency + '":"*"}}'}))
    assert "babel" in capabilities.repository_plugins
    runtime = PluginRuntime(catalog)
    source = 'import {transformAsync} from "@babel/core"; transformAsync(code, {plugins:["@vendor/plugin"]});'
    for path, expected in (("packages/admin/build.ts", 1), ("outside.ts", 1)):
        facts, diagnostics = runtime.graph_facts(FileArtifact(path, source), capabilities)
        assert not diagnostics
        selected = [f for f in facts if f.relation == "configures-module"]
        assert len(selected) == expected
        if selected:
            assert selected[0].contributing_plugin_ids == ("babel",)


@pytest.mark.parametrize("declaration,call", [
    ('const {transformAsync} = await import("@babel/core");', 'transformAsync(code, OPTIONS)'),
    ('const {transformAsync: compile} = await import("@babel/core");', 'compile(code, OPTIONS)'),
    ('const babel = await import("@babel/core");', 'babel.transformSync(code, OPTIONS)'),
    ('const {default: react} = await import("@vitejs/plugin-react");', 'react({babel: OPTIONS})'),
    ('import {createRequire} from "node:module"; const load = createRequire(import.meta.url); const path = load.resolve("@babel/core"); const {transformAsync} = await import(path);', 'transformAsync(code, OPTIONS)'),
    ('import * as node from "node:module"; const load = node.createRequire(import.meta.url); const babel = await import(load.resolve("@babel/core"));', 'babel.transformSync(code, OPTIONS)'),
    ('import {createRequire as factory} from "module"; const load = factory(import.meta.url); const path = load.resolve("@babel/core"); const promise = import(path); const {transformAsync: compile} = await promise;', 'compile(code, OPTIONS)'),
])
def test_literal_dynamic_loaders_preserve_configured_module_evidence(extract, declaration, call):
    facts = extract(declaration + '\n' + call.replace('OPTIONS', '{plugins:["@vendor/plugin"], parserOpts:{plugins:["jsx"]}}'))
    assert len(facts) == 1 and facts[0].target == '@vendor/plugin' and facts[0].line == 2
    assert dict(facts[0].attributes)['loader'] in {'@babel/core', '@vitejs/plugin-react'}


@pytest.mark.parametrize("source", [
    'const {transformAsync} = import("@babel/core"); transformAsync(code, {plugins:["wrong"]});',
    'const {transformAsync} = await import(unknown); transformAsync(code, {plugins:["wrong"]}); /* @babel/core */',
    'const {transformAsync} = await import("other"); transformAsync(code, {plugins:["wrong"]}); /* @babel/core */',
    'const {transformAsync = fallback} = await import("@babel/core"); transformAsync(code, {plugins:["wrong"]});',
    'let babel = await import("@babel/core"); babel.transformAsync(code, {plugins:["wrong"]});',
    'const babel = await import("@babel/core"); babel.transformAsync = other; babel.transformAsync(code, {plugins:["wrong"]});',
    'const babel = await import("@babel/core"); function f(babel) { babel.transformAsync(code, {plugins:["wrong"]}); }',
    'function f() {const babel = await import("@babel/core");} babel.transformAsync(code, {plugins:["wrong"]});',
    'babel.transformAsync(code, {plugins:["wrong"]}); const babel = await import("@babel/core");',
    'import {createRequire} from "other"; const load=createRequire(base); const path=load.resolve("@babel/core"); const babel=await import(path); babel.transformAsync(code, {plugins:["wrong"]});',
    'import {createRequire} from "node:module"; const load=createRequire(base); load.resolve=other; const babel=await import(load.resolve("@babel/core")); babel.transformAsync(code, {plugins:["wrong"]});',
    'import {createRequire} from "node:module"; const load=createRequire(base); const path=load.resolve("@babel/core"); path=other; const babel=await import(path); babel.transformAsync(code, {plugins:["wrong"]});',
    'import {createRequire} from "node:module"; const load=createRequire(base); const babel=await import(load.resolve(name)); babel.transformAsync(code, {plugins:["wrong"]}); /* @babel/core */',
    'const cycle = cycle; const babel=await import(cycle); babel.transformAsync(code, {plugins:["wrong"]}); /* @babel/core */',
])
def test_dynamic_loader_provenance_rejects_unsupported_or_mutated_bindings(extract, source):
    assert extract(source) == []


def test_typescript_assertion_does_not_supply_runtime_module_provenance():
    plugin = PluginCatalog.discover(Path(__file__).resolve().parents[3]).implementation("babel")
    template = '''import {createRequire} from "node:module";
function plugin(root: string) {
  const load = createRequire(root);
  const path = load.resolve("@babel/core");
  return {async transform(code: string) {
    const {transformAsync} = (await import(TARGET)) as typeof import("@babel/core");
    return transformAsync(code, {plugins: ["@vendor/plugin"]});
  }};
}'''
    for target, expected in [('path', 1), ('unknown', 0), ('"other"', 0)]:
        outcome = plugin.index_file(FileArtifact('vite-config.ts', template.replace('TARGET', target)))
        assert outcome.status.value == 'handled'
        assert len(outcome.value) == expected
        if expected:
            assert outcome.value[0].line == 7 and outcome.value[0].target == '@vendor/plugin'


def test_babel_detection_uses_real_nested_manifest_and_respects_project_source_root(tmp_path):
    from codecrow_plugins import PluginRuntime, ProjectSelector, build_repository_facts
    catalog = PluginCatalog.discover(Path(__file__).resolve().parents[3])
    sources = {
        'packages/admin/package.json': '{"devDependencies":{"@babel/core":"*"}}',
        'packages/core/build.ts': 'import {transformAsync} from "@babel/core"; transformAsync(code,{plugins:["@vendor/plugin"]});',
        'unrelated.ts': 'const options = {plugins:["not-a-module"]};',
    }
    for path, text in sources.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    capabilities = ProjectSelector(catalog.registry).select(build_repository_facts(
        tmp_path, 'head', sources, catalog.registry))
    runtime = PluginRuntime(catalog)
    for path in ('packages/core/build.ts', 'unrelated.ts'):
        facts, diagnostics = runtime.graph_facts(FileArtifact(path, sources[path]), capabilities)
        assert not diagnostics
        selected = [f for f in facts if f.relation == 'configures-module']
        assert len(selected) == (1 if path.startswith('packages/core/') else 0)
    scoped = ProjectSelector(catalog.registry).select(build_repository_facts(
        tmp_path, 'head', sources, catalog.registry, source_root='packages/core'))
    assert 'babel' not in scoped.repository_plugins


def test_type_only_loader_imports_do_not_establish_runtime_configuration():
    plugin = PluginCatalog.discover(Path(__file__).resolve().parents[3]).implementation("babel")
    for declaration in ('import type { transformAsync } from "@babel/core";',
                        'import { type transformAsync } from "@babel/core";'):
        outcome = plugin.index_file(FileArtifact("build.ts", declaration + 'transformAsync(code, {plugins:["not-runtime"]});'))
        assert outcome.status.value == "handled" and outcome.value == ()
