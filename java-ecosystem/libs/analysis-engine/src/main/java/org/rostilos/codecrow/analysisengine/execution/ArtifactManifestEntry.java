package org.rostilos.codecrow.analysisengine.execution;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonValue;

import java.util.Objects;
import java.util.regex.Pattern;

/**
 * Immutable persistence coordinates for one execution-owned artifact.
 */
public record ArtifactManifestEntry(
        @JsonProperty(value = "executionId", required = true) String executionId,
        @JsonProperty(value = "artifactId", required = true) String artifactId,
        @JsonProperty(value = "contentKey", required = true) String contentKey,
        @JsonProperty(value = "snapshotSha", required = true) String snapshotSha,
        @JsonProperty(value = "contentDigest", required = true) String contentDigest,
        @JsonProperty(value = "byteLength", required = true) long byteLength,
        @JsonProperty(value = "kind", required = true) Kind kind,
        @JsonProperty(value = "artifactSchemaVersion", required = true) String artifactSchemaVersion,
        @JsonProperty(value = "producer", required = true) String producer,
        @JsonProperty(value = "producerVersion", required = true) String producerVersion) {
    private static final Pattern IDENTIFIER =
            Pattern.compile("[A-Za-z0-9][A-Za-z0-9._:-]{0,159}");
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern REVISION = Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");
    private static final Pattern VERSION = Pattern.compile("[a-z0-9][a-z0-9._-]{0,63}");

    /** Artifact roles understood by the execution-manifest boundary. */
    public enum Kind {
        RAW_DIFF("raw-diff"),
        SOURCE_FILE("source-file"),
        PR_ENRICHMENT("pr-enrichment"),
        REVIEW_OUTPUT("review-output");

        private final String wireValue;

        Kind(String wireValue) {
            this.wireValue = wireValue;
        }

        @JsonValue
        public String wireValue() {
            return wireValue;
        }

        @JsonCreator
        public static Kind fromWireValue(String wireValue) {
            for (Kind kind : values()) {
                if (kind.wireValue.equals(wireValue)) {
                    return kind;
                }
            }
            throw new IllegalArgumentException("unsupported artifact kind: " + wireValue);
        }
    }

    @JsonCreator(mode = JsonCreator.Mode.PROPERTIES)
    public ArtifactManifestEntry {
        requireMatch(executionId, IDENTIFIER, "executionId");
        requireMatch(artifactId, IDENTIFIER, "artifactId");
        if (contentKey == null
                || contentKey.isBlank()
                || contentKey.length() > 1024
                || contentKey.indexOf('\0') >= 0) {
            throw new IllegalArgumentException("contentKey is invalid");
        }
        requireMatch(snapshotSha, REVISION, "snapshotSha");
        requireMatch(contentDigest, SHA_256, "contentDigest");
        if (byteLength < 0) {
            throw new IllegalArgumentException("byteLength must not be negative");
        }
        Objects.requireNonNull(kind, "kind");
        requireMatch(artifactSchemaVersion, VERSION, "artifactSchemaVersion");
        requireMatch(producer, IDENTIFIER, "producer");
        requireMatch(producerVersion, VERSION, "producerVersion");
    }

    private static void requireMatch(String value, Pattern pattern, String field) {
        if (value == null || !pattern.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " is invalid");
        }
    }
}
