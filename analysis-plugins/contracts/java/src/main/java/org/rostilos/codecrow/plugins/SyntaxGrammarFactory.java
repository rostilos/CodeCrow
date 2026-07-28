package org.rostilos.codecrow.plugins;

import org.treesitter.TSLanguage;

/** Creates the tree-sitter grammar owned by a language plugin. */
@FunctionalInterface
public interface SyntaxGrammarFactory {
    TSLanguage create();
}
