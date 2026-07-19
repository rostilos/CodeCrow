package org.rostilos.codecrow.analysisengine.execution;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.core.JsonGenerator;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonToken;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationContext;
import com.fasterxml.jackson.databind.JsonDeserializer;
import com.fasterxml.jackson.databind.JsonMappingException;
import com.fasterxml.jackson.databind.JsonSerializer;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializerProvider;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;
import com.fasterxml.jackson.databind.annotation.JsonSerialize;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.HexFormat;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Pattern;

/**
 * Immutable, self-verifying identity and artifact-manifest coordinates for one
 * exact pull-request execution.
 */
public record ImmutableExecutionManifest(
        @JsonProperty(value = "schemaVersion", required = true) int schemaVersion,
        @JsonProperty(value = "executionId", required = true) String executionId,
        @JsonProperty(value = "projectId", required = true) long projectId,
        @JsonProperty(value = "repositoryId", required = true) String repositoryId,
        @JsonProperty(value = "pullRequestId", required = true) long pullRequestId,
        @JsonProperty(value = "baseSha", required = true) String baseSha,
        @JsonProperty(value = "headSha", required = true) String headSha,
        @JsonProperty(value = "mergeBaseSha", required = true) String mergeBaseSha,
        @JsonProperty(value = "diffArtifactId", required = true) String diffArtifactId,
        @JsonProperty(value = "diffDigest", required = true) String diffDigest,
        @JsonProperty(value = "diffByteLength", required = true) long diffByteLength,
        @JsonProperty(value = "diffArtifactKind", required = true) String diffArtifactKind,
        @JsonProperty(value = "diffArtifactProducer", required = true) String diffArtifactProducer,
        @JsonProperty(value = "diffArtifactProducerVersion", required = true) String diffArtifactProducerVersion,
        @JsonProperty(value = "artifactSchemaVersion", required = true) String artifactSchemaVersion,
        @JsonProperty(value = "policyVersion", required = true) String policyVersion,
        @JsonProperty(value = "creationFence", required = true) String creationFence,
        @JsonProperty(value = "createdAt", required = true)
        @JsonSerialize(using = CanonicalInstantSerializer.class)
        @JsonDeserialize(using = CanonicalInstantDeserializer.class)
        Instant createdAt,
        @JsonProperty(value = "inputArtifacts", required = true)
        List<ArtifactManifestEntry> inputArtifacts,
        @JsonProperty(value = "artifactManifestDigest", required = true) String artifactManifestDigest) {
    public static final int CURRENT_SCHEMA_VERSION = 1;
    public static final String CURRENT_ARTIFACT_SCHEMA_VERSION = "review-artifact-v1";
    public static final String RAW_DIFF_ARTIFACT_KIND = "raw-diff";
    public static final String RAW_DIFF_CONTENT_KEY = "pull-request.diff";
    public static final String PR_ENRICHMENT_CONTENT_KEY = "pr-enrichment.json";
    public static final String RAG_EXECUTION_CONFIG_CONTENT_KEY = "rag-execution-config-v1.json";

    private static final Pattern IDENTIFIER = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._:-]{0,159}");
    private static final Pattern REPOSITORY_ID = Pattern.compile(
            "[a-z0-9][a-z0-9._-]{0,31}:[A-Za-z0-9._-]{1,128}(?:/[A-Za-z0-9._-]{1,128})+");
    private static final Pattern REVISION = Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern VERSION = Pattern.compile("[a-z0-9][a-z0-9._-]{0,63}");
    private static final Pattern CANONICAL_INSTANT = Pattern.compile(
            "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                    + "(?:\\.[0-9]{3}|\\.[0-9]{6})?Z");
    private static final ObjectMapper CANONICAL_JSON = new ObjectMapper();

    @JsonCreator(mode = JsonCreator.Mode.PROPERTIES)
    public ImmutableExecutionManifest {
        validateCoordinates(
                schemaVersion,
                executionId,
                projectId,
                repositoryId,
                pullRequestId,
                baseSha,
                headSha,
                mergeBaseSha,
                diffArtifactId,
                diffDigest,
                diffByteLength,
                diffArtifactKind,
                diffArtifactProducer,
                diffArtifactProducerVersion,
                artifactSchemaVersion,
                policyVersion,
                creationFence,
                createdAt);
        inputArtifacts = requireCanonicalInputArtifacts(
                inputArtifacts,
                executionId,
                headSha,
                diffArtifactId,
                diffDigest,
                diffByteLength,
                diffArtifactProducer,
                diffArtifactProducerVersion,
                artifactSchemaVersion);
        requireSha256(artifactManifestDigest, "artifactManifestDigest");
        String expectedDigest = computeArtifactManifestDigest(
                schemaVersion,
                executionId,
                projectId,
                repositoryId,
                pullRequestId,
                baseSha,
                headSha,
                mergeBaseSha,
                diffArtifactId,
                diffDigest,
                diffByteLength,
                diffArtifactKind,
                diffArtifactProducer,
                diffArtifactProducerVersion,
                artifactSchemaVersion,
                policyVersion,
                creationFence,
                createdAt,
                inputArtifacts);
        if (!constantTimeEquals(expectedDigest, artifactManifestDigest)) {
            throw new IllegalArgumentException(
                    "artifactManifestDigest does not match immutable coordinates");
        }
    }

    /** Creates a v1 manifest and computes its canonical digest. */
    public static ImmutableExecutionManifest create(
            int schemaVersion,
            String executionId,
            long projectId,
            String repositoryId,
            long pullRequestId,
            String baseSha,
            String headSha,
            String mergeBaseSha,
            String diffArtifactId,
            String diffDigest,
            long diffByteLength,
            String diffArtifactKind,
            String diffArtifactProducer,
            String diffArtifactProducerVersion,
            String artifactSchemaVersion,
            String policyVersion,
            String creationFence,
            Instant createdAt) {
        ArtifactManifestEntry initialDiff = new ArtifactManifestEntry(
                executionId,
                diffArtifactId,
                RAW_DIFF_CONTENT_KEY,
                headSha,
                diffDigest,
                diffByteLength,
                ArtifactManifestEntry.Kind.RAW_DIFF,
                artifactSchemaVersion,
                diffArtifactProducer,
                diffArtifactProducerVersion);
        return create(
                schemaVersion,
                executionId,
                projectId,
                repositoryId,
                pullRequestId,
                baseSha,
                headSha,
                mergeBaseSha,
                diffArtifactId,
                diffDigest,
                diffByteLength,
                diffArtifactKind,
                diffArtifactProducer,
                diffArtifactProducerVersion,
                artifactSchemaVersion,
                policyVersion,
                creationFence,
                createdAt,
                List.of(initialDiff));
    }

    /** Creates a v1 manifest over the complete immutable input-artifact set. */
    public static ImmutableExecutionManifest create(
            int schemaVersion,
            String executionId,
            long projectId,
            String repositoryId,
            long pullRequestId,
            String baseSha,
            String headSha,
            String mergeBaseSha,
            String diffArtifactId,
            String diffDigest,
            long diffByteLength,
            String diffArtifactKind,
            String diffArtifactProducer,
            String diffArtifactProducerVersion,
            String artifactSchemaVersion,
            String policyVersion,
            String creationFence,
            Instant createdAt,
            List<ArtifactManifestEntry> inputArtifacts) {
        validateCoordinates(
                schemaVersion,
                executionId,
                projectId,
                repositoryId,
                pullRequestId,
                baseSha,
                headSha,
                mergeBaseSha,
                diffArtifactId,
                diffDigest,
                diffByteLength,
                diffArtifactKind,
                diffArtifactProducer,
                diffArtifactProducerVersion,
                artifactSchemaVersion,
                policyVersion,
                creationFence,
                createdAt);
        List<ArtifactManifestEntry> canonicalArtifacts = inputArtifacts == null
                ? null
                : inputArtifacts.stream()
                        .sorted(Comparator.comparing(ArtifactManifestEntry::artifactId))
                        .toList();
        canonicalArtifacts = requireCanonicalInputArtifacts(
                canonicalArtifacts,
                executionId,
                headSha,
                diffArtifactId,
                diffDigest,
                diffByteLength,
                diffArtifactProducer,
                diffArtifactProducerVersion,
                artifactSchemaVersion);
        String digest = computeArtifactManifestDigest(
                schemaVersion,
                executionId,
                projectId,
                repositoryId,
                pullRequestId,
                baseSha,
                headSha,
                mergeBaseSha,
                diffArtifactId,
                diffDigest,
                diffByteLength,
                diffArtifactKind,
                diffArtifactProducer,
                diffArtifactProducerVersion,
                artifactSchemaVersion,
                policyVersion,
                creationFence,
                createdAt,
                canonicalArtifacts);
        return new ImmutableExecutionManifest(
                schemaVersion,
                executionId,
                projectId,
                repositoryId,
                pullRequestId,
                baseSha,
                headSha,
                mergeBaseSha,
                diffArtifactId,
                diffDigest,
                diffByteLength,
                diffArtifactKind,
                diffArtifactProducer,
                diffArtifactProducerVersion,
                artifactSchemaVersion,
                policyVersion,
                creationFence,
                createdAt,
                canonicalArtifacts,
                digest);
    }

    /** Rejects a persisted or downstream manifest from any other execution. */
    public void requireSameCoordinates(ImmutableExecutionManifest expected) {
        Objects.requireNonNull(expected, "expected");
        if (!equals(expected)) {
            throw new IllegalArgumentException("immutable execution coordinates do not match");
        }
    }

    /** Verifies that raw diff bytes are exactly those bound by this manifest. */
    public void verifyRawDiff(byte[] rawDiff) {
        Objects.requireNonNull(rawDiff, "rawDiff");
        if (rawDiff.length != diffByteLength) {
            throw new IllegalArgumentException(
                    "raw diff byte length does not match immutable manifest");
        }
        byte[] expected = HexFormat.of().parseHex(diffDigest);
        byte[] observed = digest(rawDiff);
        if (!MessageDigest.isEqual(expected, observed)) {
            throw new IllegalArgumentException("raw diff digest does not match immutable manifest");
        }
    }

    private static void validateCoordinates(
            int schemaVersion,
            String executionId,
            long projectId,
            String repositoryId,
            long pullRequestId,
            String baseSha,
            String headSha,
            String mergeBaseSha,
            String diffArtifactId,
            String diffDigest,
            long diffByteLength,
            String diffArtifactKind,
            String diffArtifactProducer,
            String diffArtifactProducerVersion,
            String artifactSchemaVersion,
            String policyVersion,
            String creationFence,
            Instant createdAt) {
        if (schemaVersion != CURRENT_SCHEMA_VERSION) {
            throw new IllegalArgumentException("schemaVersion must be 1");
        }
        requireMatch(executionId, IDENTIFIER, "executionId");
        if (projectId <= 0) {
            throw new IllegalArgumentException("projectId must be positive");
        }
        requireMatch(repositoryId, REPOSITORY_ID, "repositoryId");
        if (pullRequestId <= 0) {
            throw new IllegalArgumentException("pullRequestId must be positive");
        }
        requireMatch(baseSha, REVISION, "baseSha");
        requireMatch(headSha, REVISION, "headSha");
        requireMatch(mergeBaseSha, REVISION, "mergeBaseSha");
        requireMatch(diffArtifactId, IDENTIFIER, "diffArtifactId");
        requireSha256(diffDigest, "diffDigest");
        if (diffByteLength < 0) {
            throw new IllegalArgumentException("diffByteLength must not be negative");
        }
        if (!RAW_DIFF_ARTIFACT_KIND.equals(diffArtifactKind)) {
            throw new IllegalArgumentException("diffArtifactKind must be raw-diff");
        }
        requireMatch(diffArtifactProducer, IDENTIFIER, "diffArtifactProducer");
        requireMatch(diffArtifactProducerVersion, VERSION, "diffArtifactProducerVersion");
        if (!CURRENT_ARTIFACT_SCHEMA_VERSION.equals(artifactSchemaVersion)) {
            throw new IllegalArgumentException("artifactSchemaVersion must be review-artifact-v1");
        }
        requireMatch(policyVersion, VERSION, "policyVersion");
        requireMatch(creationFence, IDENTIFIER, "creationFence");
        canonicalInstant(createdAt);
    }

    private static String computeArtifactManifestDigest(
            int schemaVersion,
            String executionId,
            long projectId,
            String repositoryId,
            long pullRequestId,
            String baseSha,
            String headSha,
            String mergeBaseSha,
            String diffArtifactId,
            String diffDigest,
            long diffByteLength,
            String diffArtifactKind,
            String diffArtifactProducer,
            String diffArtifactProducerVersion,
            String artifactSchemaVersion,
            String policyVersion,
            String creationFence,
            Instant createdAt,
            List<ArtifactManifestEntry> inputArtifacts) {
        Map<String, Object> canonical = new TreeMap<>();
        canonical.put("schemaVersion", schemaVersion);
        canonical.put("executionId", executionId);
        canonical.put("projectId", projectId);
        canonical.put("repositoryId", repositoryId);
        canonical.put("pullRequestId", pullRequestId);
        canonical.put("baseSha", baseSha);
        canonical.put("headSha", headSha);
        canonical.put("mergeBaseSha", mergeBaseSha);
        canonical.put("diffArtifactId", diffArtifactId);
        canonical.put("diffDigest", diffDigest);
        canonical.put("diffByteLength", diffByteLength);
        canonical.put("diffArtifactKind", diffArtifactKind);
        canonical.put("diffArtifactProducer", diffArtifactProducer);
        canonical.put("diffArtifactProducerVersion", diffArtifactProducerVersion);
        canonical.put("artifactSchemaVersion", artifactSchemaVersion);
        canonical.put("policyVersion", policyVersion);
        canonical.put("creationFence", creationFence);
        canonical.put("createdAt", canonicalInstant(createdAt));
        List<Map<String, Object>> canonicalArtifacts = new ArrayList<>();
        for (ArtifactManifestEntry entry : inputArtifacts) {
            Map<String, Object> artifact = new TreeMap<>();
            artifact.put("artifactId", entry.artifactId());
            artifact.put("artifactSchemaVersion", entry.artifactSchemaVersion());
            artifact.put("byteLength", entry.byteLength());
            artifact.put("contentDigest", entry.contentDigest());
            artifact.put("contentKey", entry.contentKey());
            artifact.put("executionId", entry.executionId());
            artifact.put("kind", entry.kind().wireValue());
            artifact.put("producer", entry.producer());
            artifact.put("producerVersion", entry.producerVersion());
            artifact.put("snapshotSha", entry.snapshotSha());
            canonicalArtifacts.add(artifact);
        }
        canonical.put("inputArtifacts", canonicalArtifacts);
        try {
            return sha256(CANONICAL_JSON.writeValueAsBytes(canonical));
        } catch (JsonProcessingException error) {
            throw new IllegalStateException("immutable manifest is not canonically serializable", error);
        }
    }

    private static void requireSha256(String value, String field) {
        requireMatch(value, SHA_256, field);
    }

    private static List<ArtifactManifestEntry> requireCanonicalInputArtifacts(
            List<ArtifactManifestEntry> artifacts,
            String executionId,
            String headSha,
            String diffArtifactId,
            String diffDigest,
            long diffByteLength,
            String diffProducer,
            String diffProducerVersion,
            String artifactSchemaVersion) {
        if (artifacts == null || artifacts.isEmpty()) {
            throw new IllegalArgumentException("inputArtifacts must not be empty");
        }
        List<ArtifactManifestEntry> immutable = List.copyOf(artifacts);
        List<ArtifactManifestEntry> sorted = immutable.stream()
                .sorted(Comparator.comparing(ArtifactManifestEntry::artifactId))
                .toList();
        if (!immutable.equals(sorted)) {
            throw new IllegalArgumentException("inputArtifacts must use canonical artifactId order");
        }
        Set<String> artifactIds = new HashSet<>();
        Set<String> contentKeys = new HashSet<>();
        ArtifactManifestEntry initialDiff = null;
        int enrichmentArtifacts = 0;
        int executionConfigArtifacts = 0;
        for (ArtifactManifestEntry artifact : immutable) {
            if (!artifactIds.add(artifact.artifactId())) {
                throw new IllegalArgumentException("inputArtifacts contain a duplicate artifactId");
            }
            if (!contentKeys.add(artifact.contentKey())) {
                throw new IllegalArgumentException("inputArtifacts contain a duplicate contentKey");
            }
            if (!executionId.equals(artifact.executionId())) {
                throw new IllegalArgumentException("input artifact belongs to another execution");
            }
            if (!headSha.equals(artifact.snapshotSha())) {
                throw new IllegalArgumentException("input artifact belongs to another snapshot");
            }
            if (!artifactSchemaVersion.equals(artifact.artifactSchemaVersion())) {
                throw new IllegalArgumentException("input artifact schema conflicts with manifest");
            }
            switch (artifact.kind()) {
                case RAW_DIFF -> {
                    if (initialDiff != null) {
                        throw new IllegalArgumentException(
                                "inputArtifacts must contain exactly one raw diff");
                    }
                    initialDiff = artifact;
                }
                case PR_ENRICHMENT -> enrichmentArtifacts++;
                case EXECUTION_CONFIG -> executionConfigArtifacts++;
                case SOURCE_FILE -> { }
                case REVIEW_OUTPUT -> throw new IllegalArgumentException(
                        "review output cannot be an initial input artifact");
            }
        }
        if (initialDiff == null) {
            throw new IllegalArgumentException(
                    "inputArtifacts must contain exactly one raw diff");
        }
        ArtifactManifestEntry expectedDiff = new ArtifactManifestEntry(
                executionId,
                diffArtifactId,
                RAW_DIFF_CONTENT_KEY,
                headSha,
                diffDigest,
                diffByteLength,
                ArtifactManifestEntry.Kind.RAW_DIFF,
                artifactSchemaVersion,
                diffProducer,
                diffProducerVersion);
        if (!expectedDiff.equals(initialDiff)) {
            throw new IllegalArgumentException(
                    "raw diff input artifact conflicts with manifest coordinates");
        }
        if (enrichmentArtifacts > 1) {
            throw new IllegalArgumentException(
                    "inputArtifacts contain multiple enrichment documents");
        }
        if (executionConfigArtifacts > 1) {
            throw new IllegalArgumentException(
                    "inputArtifacts contain multiple execution config documents");
        }
        return immutable;
    }

    private static void requireMatch(String value, Pattern pattern, String field) {
        if (value == null || !pattern.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " is invalid");
        }
    }

    private static boolean constantTimeEquals(String first, String second) {
        return MessageDigest.isEqual(
                first.getBytes(StandardCharsets.US_ASCII),
                second.getBytes(StandardCharsets.US_ASCII));
    }

    private static String sha256(byte[] value) {
        return HexFormat.of().formatHex(digest(value));
    }

    private static byte[] digest(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }

    private static String canonicalInstant(Instant value) {
        Objects.requireNonNull(value, "createdAt");
        if (value.getNano() % 1_000 != 0) {
            throw new IllegalArgumentException(
                    "createdAt must not exceed microsecond precision");
        }
        String canonical = value.toString();
        if (!CANONICAL_INSTANT.matcher(canonical).matches()) {
            throw new IllegalArgumentException("createdAt is outside the canonical wire range");
        }
        return canonical;
    }

    /** Writes the only timestamp spelling accepted by the cross-language digest. */
    public static final class CanonicalInstantSerializer extends JsonSerializer<Instant> {
        @Override
        public void serialize(
                Instant value,
                JsonGenerator generator,
                SerializerProvider serializers) throws IOException {
            generator.writeString(canonicalInstant(value));
        }
    }

    /** Rejects numeric, offset, redundant-fraction, and nanosecond wire values. */
    public static final class CanonicalInstantDeserializer extends JsonDeserializer<Instant> {
        @Override
        public Instant deserialize(
                JsonParser parser,
                DeserializationContext context) throws IOException {
            if (!parser.hasToken(JsonToken.VALUE_STRING)) {
                throw JsonMappingException.from(
                        parser, "createdAt must be a canonical UTC string");
            }
            String wireValue = parser.getText();
            if (!CANONICAL_INSTANT.matcher(wireValue).matches()) {
                throw context.weirdStringException(
                        wireValue,
                        Instant.class,
                        "createdAt must use canonical UTC Z notation");
            }
            try {
                Instant parsed = Instant.parse(wireValue);
                if (!canonicalInstant(parsed).equals(wireValue)) {
                    throw new IllegalArgumentException("createdAt is not canonically encoded");
                }
                return parsed;
            } catch (RuntimeException error) {
                throw context.weirdStringException(
                        wireValue,
                        Instant.class,
                        "createdAt is not a canonical instant");
            }
        }
    }
}
