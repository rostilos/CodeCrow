package org.rostilos.codecrow.plugins.haskell;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.OutcomeStatus;
import org.treesitter.TSQuery;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

class HaskellPluginTest {

    @Test
    void packages_its_descriptor_grammar_and_scope_query() throws IOException {
        HaskellPlugin plugin = new HaskellPlugin();
        var outcome = plugin.syntaxContribution();

        assertThat(plugin.descriptor().id()).isEqualTo("haskell");
        assertThat(outcome.status()).isEqualTo(OutcomeStatus.HANDLED);
        var syntax = outcome.value();
        assertThat(syntax.languageId()).isEqualTo("haskell");
        var grammar = syntax.grammarFactory().create();
        assertThat(grammar).isNotNull();
        try (var input = HaskellPlugin.class.getClassLoader()
                .getResourceAsStream(syntax.scopeQueryResource())) {
            assertThat(input).isNotNull();
            String query = new String(input.readAllBytes(), StandardCharsets.UTF_8);
            try (TSQuery ignored = new TSQuery(grammar, query)) {
                // Construction validates all node types, fields, and captures.
            }
        }
    }
}
