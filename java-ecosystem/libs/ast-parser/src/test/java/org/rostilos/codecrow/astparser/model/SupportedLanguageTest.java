package org.rostilos.codecrow.astparser.model;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.SyntaxContribution;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class SupportedLanguageTest {

    @Test
    void runtime_language_is_derived_only_from_a_plugin_contribution() {
        SupportedLanguage language = new SupportedLanguage(new SyntaxContribution(
                "fixture", List.of(".fixture"), "", () -> null));

        assertThat(language.id()).isEqualTo("fixture");
        assertThat(language.extensions()).containsExactly(".fixture");
        assertThat(language.toString()).isEqualTo("fixture");
    }

    @Test
    void language_identity_is_stable_across_plugin_reload() {
        SyntaxContribution first = new SyntaxContribution(
                "fixture", List.of(".fixture"), "", () -> null);
        SyntaxContribution second = new SyntaxContribution(
                "fixture", List.of(".fixture"), "", () -> null);

        assertThat(new SupportedLanguage(first)).isEqualTo(new SupportedLanguage(second));
    }
}
