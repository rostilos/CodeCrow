package org.rostilos.codecrow.analysisengine.execution;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RagExecutionConfigV1Test {
    private static final String BASE_SHA = "a".repeat(40);

    @Test
    void canonicalBytesFreezeIndexAndEveryProcessingVersion() {
        RagExecutionConfigV1 config = new RagExecutionConfigV1(
                1,
                "rag-commit-" + BASE_SHA,
                "tree-sitter-v7",
                "ast-code-splitter-v4",
                "text-embedding-3-small-v2");

        assertThat(new String(config.canonicalBytes(), StandardCharsets.UTF_8))
                .isEqualTo("{\"chunkerVersion\":\"ast-code-splitter-v4\","
                        + "\"embeddingVersion\":\"text-embedding-3-small-v2\","
                        + "\"indexVersion\":\"rag-commit-" + BASE_SHA + "\","
                        + "\"parserVersion\":\"tree-sitter-v7\","
                        + "\"schemaVersion\":1}");
        config.requireCompatibleBaseSha(BASE_SHA);
    }

    @Test
    void rejectsMovingOrWrongBaseIndexAndMalformedVersions() {
        assertThatThrownBy(() -> new RagExecutionConfigV1(
                1, "rag-main", "parser-v1", "chunker-v1", "embedding-v1"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("indexVersion");
        assertThatThrownBy(() -> new RagExecutionConfigV1(
                1,
                "rag-commit-" + "b".repeat(40),
                "parser-v1",
                "chunker-v1",
                "embedding-v1").requireCompatibleBaseSha(BASE_SHA))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("baseSha");
        assertThatThrownBy(() -> new RagExecutionConfigV1(
                1, "rag-disabled", "parser v1", "chunker-v1", "embedding-v1"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("parserVersion");
    }
}
