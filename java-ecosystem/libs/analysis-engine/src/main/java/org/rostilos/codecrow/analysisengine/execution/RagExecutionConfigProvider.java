package org.rostilos.codecrow.analysisengine.execution;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** Freezes deployment-selected RAG processing versions into each execution. */
@Component
public final class RagExecutionConfigProvider {
    private final String parserVersion;
    private final String chunkerVersion;
    private final String embeddingVersion;

    public RagExecutionConfigProvider(
            @Value("${RAG_PARSER_VERSION:tree-sitter-v1}") String parserVersion,
            @Value("${RAG_CHUNKER_VERSION:ast-code-splitter-v1}") String chunkerVersion,
            @Value("${RAG_EMBEDDING_VERSION:configured-v1}") String embeddingVersion) {
        // Validate the deployment configuration at bean construction instead
        // of allowing a malformed identity to reach the queue.
        RagExecutionConfigV1 validated = new RagExecutionConfigV1(
                RagExecutionConfigV1.CURRENT_SCHEMA_VERSION,
                "rag-disabled",
                parserVersion,
                chunkerVersion,
                embeddingVersion);
        this.parserVersion = validated.parserVersion();
        this.chunkerVersion = validated.chunkerVersion();
        this.embeddingVersion = validated.embeddingVersion();
    }

    public static RagExecutionConfigProvider defaults() {
        return new RagExecutionConfigProvider(
                RagExecutionConfigV1.DEFAULT_PARSER_VERSION,
                RagExecutionConfigV1.DEFAULT_CHUNKER_VERSION,
                RagExecutionConfigV1.DEFAULT_EMBEDDING_VERSION);
    }

    public RagExecutionConfigV1 freeze(String indexVersion, String baseSha) {
        RagExecutionConfigV1 selected = new RagExecutionConfigV1(
                RagExecutionConfigV1.CURRENT_SCHEMA_VERSION,
                indexVersion,
                parserVersion,
                chunkerVersion,
                embeddingVersion);
        selected.requireCompatibleBaseSha(baseSha);
        return selected;
    }
}
