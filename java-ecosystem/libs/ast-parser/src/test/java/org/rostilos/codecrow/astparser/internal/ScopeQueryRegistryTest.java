package org.rostilos.codecrow.astparser.internal;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.astparser.model.SupportedLanguage;
import org.treesitter.TSQuery;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class ScopeQueryRegistryTest {

    @Test
    void available_scope_queries_compile_against_their_tree_sitter_grammars() {
        ScopeQueryRegistry registry = new ScopeQueryRegistry();
        List<String> failures = new ArrayList<>();

        for (SupportedLanguage language : SupportedLanguage.values()) {
            registry.getQuery(language).ifPresent(query -> {
                try (TSQuery ignored = new TSQuery(language.createGrammar(), query)) {
                    // Constructor validates node types, fields, and captures.
                } catch (RuntimeException e) {
                    failures.add("%s (%s): %s".formatted(
                            language, language.getScopeQueryPath(), e.getMessage()));
                }
            });
        }

        assertThat(failures).isEmpty();
    }
}
