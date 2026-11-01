from pathlib import Path

import pytest

from codecrow_plugins import FileArtifact, PluginCatalog, PluginRuntime, ProjectSelector, RepositoryFacts


@pytest.fixture
def runtime():
    catalog = PluginCatalog.discover(Path(__file__).resolve().parents[3])
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(
        revision="pinned-head", paths=("src/admin.astro",),
    ))
    assert capabilities.repository_plugins == ("astro",)
    return PluginRuntime(catalog), capabilities


def _facts(runtime, content, *, deleted=False):
    host, capabilities = runtime
    return host.graph_facts(FileArtifact("src/admin.astro", content, deleted), capabilities)


def test_component_imports_are_source_bound_and_template_keeps_fallback(runtime):
    content = '''---
import "@emdash-cms/admin/styles.css";
import { resolveLocale } from "@emdash-cms/admin/locales";
export { helper } from "./helper";
const data = await import(`./data.json`);
const dynamic = await import(`./${locale}.json`);
const computed = await import(moduleName);
---
<script>import "client-only";</script>
<style>/* import "style-only"; */</style>
'''
    facts, diagnostics = _facts(runtime, content)
    assert diagnostics == ()
    assert {(f.target, f.line) for f in facts if f.relation == "imports"} == {
        ("@emdash-cms/admin/styles.css", 2), ("@emdash-cms/admin/locales", 3),
        ("./helper", 4), ("./data.json", 5),
    }
    patterns = [f for f in facts if f.relation == "imports-pattern"]
    assert [(f.target, f.line) for f in patterns] == [("./${locale}.json", 6)]
    assert dict(patterns[0].attributes)["dynamic"] == "true"
    assert all(f.path == f.source == "src/admin.astro" for f in facts)
    assert all(f.related_paths == () and f.contributing_plugin_ids == ("astro",) for f in facts)
    assert runtime[0].syntax_contribution("src/admin.astro", runtime[1]) == (None, ())


@pytest.mark.parametrize("script", [
    'const s = "---";',
    "const s = '---';",
    "const s = `\n---\nimport 'fake';\n`;",
    "const s = `nested ${`---${1}`}`;",
    "/*\n---\nimport 'fake';\n*/",
    "// --- import 'fake';\n",
    "const r = /---/g;",
    "const r = /[\\/---]/;",
    'const value = 10 / 2;',
    'const unicode = "Київ";',
])
def test_fence_like_source_is_not_a_fence(runtime, script):
    facts, diagnostics = _facts(runtime, f"---\n{script}\nimport 'real';\n---\n<div />")
    assert diagnostics == ()
    assert [(f.target, f.line) for f in facts] == [("real", script.count('\n') + 3)]


def test_inline_fences_bom_crlf_and_exact_lines(runtime):
    facts, diagnostics = _facts(runtime, "\ufeff\r\n  --- import 'same-line';\r\nimport 'next-line'; ---<div />")
    assert diagnostics == ()
    assert {(f.target, f.line) for f in facts} == {("same-line", 2), ("next-line", 3)}


@pytest.mark.parametrize("content", [
    "---\nimport 'real';", "---\nconst invalid = ;\nimport 'real';\n---",
    "---\nconst s = `\n---\nimport 'fake';", "---\n/*\n---\nimport 'fake';",
])
def test_malformed_frontmatter_reports_recoverable_diagnostic(runtime, content):
    facts, diagnostics = _facts(runtime, content)
    assert facts == ()
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "astro-frontmatter-parse-failed"
    assert diagnostics[0].recoverable and diagnostics[0].path == "src/admin.astro"


def test_no_frontmatter_deleted_file_and_escaped_specifier_do_not_invent_imports(runtime):
    assert _facts(runtime, "<div>--- import 'fake'; ---</div>") == ((), ())
    assert _facts(runtime, "--- import 'removed'; ---", deleted=True) == ((), ())
    assert _facts(runtime, r"--- import '\x66ake'; ---") == ((), ())


def test_import_options_and_comments_preserve_the_literal_target(runtime):
    facts, diagnostics = _facts(runtime, "--- await import(/* comment */ './data.json', {with: {type: 'json'}}); ---")
    assert diagnostics == ()
    assert len(facts) == 1 and facts[0].target == "./data.json"
    assert dict(facts[0].attributes)["dynamic"] == "true"


def test_dynamic_pattern_preserves_expression_without_guessing_a_module(runtime):
    source = "---\nawait import(`@package/locales/${resolve(request)}/messages.mjs`);\n---"
    facts, diagnostics = _facts(runtime, source)
    assert diagnostics == ()
    assert len(facts) == 1 and facts[0].relation == "imports-pattern"
    assert facts[0].target == "@package/locales/${resolve(request)}/messages.mjs"
    assert facts[0].related_paths == () and facts[0].line == 2
