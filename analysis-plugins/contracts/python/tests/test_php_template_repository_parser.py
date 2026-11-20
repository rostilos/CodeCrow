from __future__ import annotations

import json
from pathlib import Path

import pytest

from codecrow_plugins import (
    FileArtifact,
    OutcomeStatus,
    PluginCatalog,
    RepositoryAnalysis,
)


pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_php")

PLUGINS_ROOT = Path(__file__).resolve().parents[3]
_REFERENCE_PREFIX = "php-template-instance-call-reference:"


def _template_symbol(content: str):
    plugin = PluginCatalog.discover(PLUGINS_ROOT).implementation("php")
    session = plugin.start_repository_analysis("template-calls").value
    session.ingest((FileArtifact("view/frontend/templates/item.phtml", content),))
    outcome = session.finish(RepositoryAnalysis())
    assert outcome.status is OutcomeStatus.HANDLED
    return next(
        symbol
        for symbol in outcome.value.symbols
        if symbol.kind == "template"
    )


def _template_references(content: str) -> tuple[dict[str, object], ...]:
    symbol = _template_symbol(content)
    return tuple(
        json.loads(value)
        for key, value in symbol.attributes
        if key.startswith(_REFERENCE_PREFIX)
    )


def test_php_template_symbol_retains_only_syntax_proven_direct_variable_calls():
    symbol = _template_symbol(r"""
        <!-- $block->htmlComment() -->
        <?php
        // $block->lineComment();
        $literal = '$block->stringCall()';
        $block->render('sku-1');
        $viewModel?->load();
        $this->escapeHtml($title);
        $dynamic->{$method}();
        ?>
    """)

    references = tuple(
        json.loads(value)
        for key, value in symbol.attributes
        if key.startswith(_REFERENCE_PREFIX)
    )

    assert {
        (reference["receiver"], reference["method"])
        for reference in references
    } == {
        ("block", "render"),
        ("viewModel", "load"),
        ("this", "escapeHtml"),
    }
    render = next(
        reference
        for reference in references
        if reference["method"] == "render"
    )
    assert render["literalStringArguments"] == {"0": "sku-1"}


def test_php_template_symbol_is_removed_when_the_template_is_deleted():
    plugin = PluginCatalog.discover(PLUGINS_ROOT).implementation("php")
    session = plugin.start_repository_analysis("template-delete").value
    path = "view/frontend/templates/item.phtml"
    session.ingest((FileArtifact(path, "<?php $block->render(); ?>"),))
    session.ingest((FileArtifact(path, "", deleted=True),))

    outcome = session.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    assert all(symbol.path != path for symbol in outcome.value.symbols)


def test_php_template_calls_stop_after_their_receiver_is_reassigned():
    references = _template_references(r"""<?php
        $block->before('old-binding');
        $viewModel->before();
        $block = $block->decorate();
        $block->after();
        $viewModel->after();
        $viewModel = $replacement;
        $viewModel->alsoAfter();
    ?>""")

    assert {
        (reference["receiver"], reference["method"])
        for reference in references
    } == {
        ("block", "before"),
        ("block", "decorate"),
        ("viewModel", "before"),
        ("viewModel", "after"),
    }


def test_php_template_calls_inside_callable_scopes_are_not_top_level_calls():
    references = _template_references(r"""<?php
        $block->topLevel();
        function renderNested($block) {
            $block->insideNamedFunction();
        }
        $closure = function () use ($block) {
            $block = $replacement;
            $block->insideClosure();
        };
        $arrow = fn () => $block->insideArrow();
        $block->afterCallableDeclarations();
        $viewModel->ordinaryTopLevel();
    ?>""")

    assert {
        (reference["receiver"], reference["method"])
        for reference in references
    } == {
        ("block", "afterCallableDeclarations"),
        ("block", "topLevel"),
        ("viewModel", "ordinaryTopLevel"),
    }


def test_php_template_parser_abstains_when_php_syntax_is_malformed():
    plugin = PluginCatalog.discover(PLUGINS_ROOT).implementation("php")
    session = plugin.start_repository_analysis("malformed-template").value
    path = "view/frontend/templates/malformed.phtml"
    session.ingest((FileArtifact(
        path,
        "<?php $block->before(); if ($condition { $block->uncertain(); ?>",
    ),))

    outcome = session.finish(RepositoryAnalysis())

    assert outcome.status is OutcomeStatus.HANDLED
    assert all(symbol.path != path for symbol in outcome.value.symbols)
