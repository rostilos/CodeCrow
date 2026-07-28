package org.rostilos.codecrow.astparser.model;

import org.rostilos.codecrow.plugins.SyntaxContribution;
import org.rostilos.codecrow.plugins.SyntaxGrammarFactory;
import org.treesitter.TSLanguage;

import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Objects;
import java.util.Set;

/**
 * Runtime language metadata supplied by a syntax plugin.
 *
 * <p>The AST host deliberately has no built-in language registry. An instance can
 * only be created from a discovered {@link SyntaxContribution}.</p>
 */
public final class SupportedLanguage {
    private final String id;
    private final Set<String> extensions;
    private final SyntaxGrammarFactory grammarFactory;

    public SupportedLanguage(SyntaxContribution contribution) {
        Objects.requireNonNull(contribution, "syntax contribution is required");
        this.id = contribution.languageId();
        this.extensions = Collections.unmodifiableSet(
                new LinkedHashSet<>(contribution.extensions()));
        this.grammarFactory = contribution.grammarFactory();
    }

    public String id() {
        return id;
    }

    public Set<String> extensions() {
        return extensions;
    }

    public TSLanguage createGrammar() {
        return grammarFactory.create();
    }

    @Override
    public boolean equals(Object other) {
        return other instanceof SupportedLanguage language && id.equals(language.id);
    }

    @Override
    public int hashCode() {
        return id.hashCode();
    }

    @Override
    public String toString() {
        return id;
    }
}
