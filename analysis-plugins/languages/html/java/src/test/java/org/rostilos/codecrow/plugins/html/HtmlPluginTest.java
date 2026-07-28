package org.rostilos.codecrow.plugins.html;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.OutcomeStatus;
import org.treesitter.TSQuery;

import java.io.IOException;
import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;

class HtmlPluginTest {

    @Test
    void packages_its_descriptor_grammar_and_scope_query() throws IOException {
        HtmlPlugin plugin = new HtmlPlugin();
        var outcome = plugin.syntaxContribution();

        assertThat(plugin.descriptor().id()).isEqualTo("html");
        assertThat(outcome.status()).isEqualTo(OutcomeStatus.HANDLED);
        var syntax = outcome.value();
        assertThat(syntax.languageId()).isEqualTo("html");
        var grammar = syntax.grammarFactory().create();
        assertThat(grammar).isNotNull();
        assertThat(syntax.scopeQueryResource()).isEmpty();
    }
}
