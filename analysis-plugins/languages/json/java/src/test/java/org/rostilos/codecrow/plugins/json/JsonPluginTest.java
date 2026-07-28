package org.rostilos.codecrow.plugins.json;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.OutcomeStatus;
import org.treesitter.TSQuery;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

class JsonPluginTest {

    @Test
    void packages_its_descriptor_grammar_and_scope_query() throws IOException {
        JsonPlugin plugin = new JsonPlugin();
        var outcome = plugin.syntaxContribution();

        assertThat(plugin.descriptor().id()).isEqualTo("json");
        assertThat(outcome.status()).isEqualTo(OutcomeStatus.HANDLED);
        var syntax = outcome.value();
        assertThat(syntax.languageId()).isEqualTo("json");
        var grammar = syntax.grammarFactory().create();
        assertThat(grammar).isNotNull();
        assertThat(syntax.scopeQueryResource()).isEmpty();
    }
}
