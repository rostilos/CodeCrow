package org.rostilos.codecrow.analysisengine.execution;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import java.util.Objects;
import java.util.regex.Pattern;

/**
 * Immutable RAG selection and processing identity for one candidate execution.
 *
 * <p>The canonical JSON bytes are persisted as an execution input artifact, so
 * the queue may expose these values only after proving that they belong to the
 * durable execution manifest.
 */
public record RagExecutionConfigV1(
        @JsonProperty(value = "schemaVersion", required = true) int schemaVersion,
        @JsonProperty(value = "indexVersion", required = true) String indexVersion,
        @JsonProperty(value = "parserVersion", required = true) String parserVersion,
        @JsonProperty(value = "chunkerVersion", required = true) String chunkerVersion,
        @JsonProperty(value = "embeddingVersion", required = true) String embeddingVersion) {
    public static final int CURRENT_SCHEMA_VERSION = 1;
    public static final String DEFAULT_PARSER_VERSION = "tree-sitter-v1";
    public static final String DEFAULT_CHUNKER_VERSION = "ast-code-splitter-v1";
    public static final String DEFAULT_EMBEDDING_VERSION = "configured-v1";

    private static final Pattern INDEX_VERSION = Pattern.compile(
            "(?:rag-disabled|rag-commit-(?:[0-9a-f]{40}|[0-9a-f]{64}))");
    private static final Pattern PROCESSING_VERSION = Pattern.compile(
            "[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}");
    private static final ObjectMapper CANONICAL_JSON = new ObjectMapper()
            .configure(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY, true)
            .configure(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true);

    @JsonCreator(mode = JsonCreator.Mode.PROPERTIES)
    public RagExecutionConfigV1 {
        if (schemaVersion != CURRENT_SCHEMA_VERSION) {
            throw new IllegalArgumentException("RAG execution config schemaVersion must be 1");
        }
        requireMatch(indexVersion, INDEX_VERSION, "indexVersion");
        requireMatch(parserVersion, PROCESSING_VERSION, "parserVersion");
        requireMatch(chunkerVersion, PROCESSING_VERSION, "chunkerVersion");
        requireMatch(embeddingVersion, PROCESSING_VERSION, "embeddingVersion");
    }

    public static RagExecutionConfigV1 defaults(String indexVersion) {
        return new RagExecutionConfigV1(
                CURRENT_SCHEMA_VERSION,
                indexVersion,
                DEFAULT_PARSER_VERSION,
                DEFAULT_CHUNKER_VERSION,
                DEFAULT_EMBEDDING_VERSION);
    }

    /** Exact canonical bytes bound into the input artifact manifest. */
    public byte[] canonicalBytes() {
        try {
            return CANONICAL_JSON.writeValueAsBytes(this);
        } catch (JsonProcessingException error) {
            throw new IllegalStateException(
                    "RAG execution config is not canonically serializable", error);
        }
    }

    public void requireCompatibleBaseSha(String baseSha) {
        Objects.requireNonNull(baseSha, "baseSha");
        String expected = "rag-commit-" + baseSha;
        if (!"rag-disabled".equals(indexVersion) && !expected.equals(indexVersion)) {
            throw new IllegalArgumentException(
                    "RAG indexVersion must be disabled or match the immutable baseSha");
        }
    }

    private static void requireMatch(String value, Pattern pattern, String field) {
        if (value == null || !pattern.matcher(value).matches()) {
            throw new IllegalArgumentException("RAG execution config " + field + " is invalid");
        }
    }
}
