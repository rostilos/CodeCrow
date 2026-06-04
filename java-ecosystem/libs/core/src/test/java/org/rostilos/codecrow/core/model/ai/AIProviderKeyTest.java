package org.rostilos.codecrow.core.model.ai;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AIProviderKey")
class AIProviderKeyTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        AIProviderKey[] values = AIProviderKey.values();
        
        assertThat(values).hasSize(6);
        assertThat(values).contains(
                AIProviderKey.OPENAI,
                AIProviderKey.OPENROUTER,
                AIProviderKey.ANTHROPIC,
                AIProviderKey.GOOGLE,
                AIProviderKey.OPENAI_COMPATIBLE,
                AIProviderKey.GOOGLE_VERTEX
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(AIProviderKey.valueOf("OPENAI")).isEqualTo(AIProviderKey.OPENAI);
        assertThat(AIProviderKey.valueOf("OPENROUTER")).isEqualTo(AIProviderKey.OPENROUTER);
        assertThat(AIProviderKey.valueOf("ANTHROPIC")).isEqualTo(AIProviderKey.ANTHROPIC);
        assertThat(AIProviderKey.valueOf("GOOGLE")).isEqualTo(AIProviderKey.GOOGLE);
        assertThat(AIProviderKey.valueOf("GOOGLE_VERTEX")).isEqualTo(AIProviderKey.GOOGLE_VERTEX);
        assertThat(AIProviderKey.valueOf("OPENAI_COMPATIBLE")).isEqualTo(AIProviderKey.OPENAI_COMPATIBLE);
    }

    @Test
    @DisplayName("ordinal values should be in correct order")
    void ordinalValuesShouldBeInCorrectOrder() {
        assertThat(AIProviderKey.OPENAI.ordinal()).isEqualTo(0);
        assertThat(AIProviderKey.OPENROUTER.ordinal()).isEqualTo(1);
        assertThat(AIProviderKey.ANTHROPIC.ordinal()).isEqualTo(2);
        assertThat(AIProviderKey.GOOGLE.ordinal()).isEqualTo(3);
        assertThat(AIProviderKey.OPENAI_COMPATIBLE.ordinal()).isEqualTo(4);
        assertThat(AIProviderKey.GOOGLE_VERTEX.ordinal()).isEqualTo(5);
    }
}
