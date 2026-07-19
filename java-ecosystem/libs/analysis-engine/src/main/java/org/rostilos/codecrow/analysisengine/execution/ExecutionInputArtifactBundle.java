package org.rostilos.codecrow.analysisengine.execution;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;
import java.util.Set;

/** Builds the complete immutable input bundle sent to the candidate worker. */
public record ExecutionInputArtifactBundle(
        List<ExecutionArtifactPayload> artifacts) {
    private static final ObjectMapper CANONICAL_JSON = new ObjectMapper()
            .configure(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY, true)
            .configure(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true)
            .addMixIn(PrEnrichmentDataDto.class, CanonicalEnrichmentMixIn.class);

    /** Excludes a legacy computed getter from the immutable artifact wire shape only. */
    private abstract static class CanonicalEnrichmentMixIn {
        @JsonIgnore
        abstract long getTotalContentSize();
    }

    public ExecutionInputArtifactBundle {
        artifacts = List.copyOf(Objects.requireNonNull(artifacts, "artifacts"));
    }

    public List<ArtifactManifestEntry> entries() {
        return artifacts.stream().map(ExecutionArtifactPayload::entry).toList();
    }

    public static ExecutionInputArtifactBundle create(
            String executionId,
            String headSha,
            String diffArtifactId,
            byte[] rawDiff,
            PrEnrichmentDataDto enrichment,
            String artifactSchemaVersion,
            String producer,
            String producerVersion) {
        return create(
                executionId,
                headSha,
                diffArtifactId,
                rawDiff,
                enrichment,
                null,
                artifactSchemaVersion,
                producer,
                producerVersion);
    }

    public static ExecutionInputArtifactBundle create(
            String executionId,
            String headSha,
            String diffArtifactId,
            byte[] rawDiff,
            PrEnrichmentDataDto enrichment,
            RagExecutionConfigV1 ragExecutionConfig,
            String artifactSchemaVersion,
            String producer,
            String producerVersion) {
        List<CanonicalInput> inputs = canonicalInputs(
                rawDiff, enrichment, ragExecutionConfig);
        List<ExecutionArtifactPayload> payloads = new ArrayList<>();
        for (CanonicalInput input : inputs) {
            String artifactId = switch (input.kind()) {
                case RAW_DIFF -> diffArtifactId;
                case SOURCE_FILE -> deterministicArtifactId(
                        "source", executionId, input.contentKey());
                case PR_ENRICHMENT -> deterministicArtifactId(
                        "enrichment", executionId, input.contentKey());
                case EXECUTION_CONFIG -> deterministicArtifactId(
                        "rag-config", executionId, input.contentKey());
                case REVIEW_OUTPUT -> throw new IllegalStateException(
                        "review output cannot be an initial input artifact");
            };
            payloads.add(payload(
                    executionId,
                    artifactId,
                    input.contentKey(),
                    headSha,
                    input.kind(),
                    input.content(),
                    artifactSchemaVersion,
                    producer,
                    producerVersion));
        }

        payloads.sort(Comparator.comparing(payload -> payload.entry().artifactId()));
        return new ExecutionInputArtifactBundle(payloads);
    }

    /**
     * Computes a collision-safe digest of every current input-artifact byte and
     * content key without depending on an execution or artifact ID. Repository,
     * snapshot, policy, index, schema, and producer identity remain separate
     * execution coordinates and must be bound by the caller.
     */
    public static String canonicalInputDigest(
            byte[] rawDiff,
            PrEnrichmentDataDto enrichment) {
        List<CanonicalInput> inputs = canonicalInputs(rawDiff, enrichment).stream()
                .sorted(Comparator
                        .comparing((CanonicalInput input) -> input.kind().wireValue())
                        .thenComparing(CanonicalInput::contentKey))
                .toList();
        StringBuilder identity = new StringBuilder("candidate-input-artifacts-v1\n");
        appendIdentityPart(identity, Integer.toString(inputs.size()));
        for (CanonicalInput input : inputs) {
            appendIdentityPart(identity, input.kind().wireValue());
            appendIdentityPart(identity, input.contentKey());
            appendIdentityPart(identity, Long.toString(input.content().length));
            appendIdentityPart(identity, sha256(input.content()));
        }
        return sha256(identity.toString().getBytes(StandardCharsets.UTF_8));
    }

    public static byte[] canonicalEnrichmentBytes(PrEnrichmentDataDto enrichment) {
        Objects.requireNonNull(enrichment, "enrichment");
        try {
            return CANONICAL_JSON.writeValueAsBytes(enrichment);
        } catch (JsonProcessingException error) {
            throw new IllegalStateException(
                    "enrichment input is not canonically serializable", error);
        }
    }

    private static List<FileContentDto> safeFileContents(PrEnrichmentDataDto enrichment) {
        return enrichment.fileContents() == null ? List.of() : enrichment.fileContents();
    }

    private static List<CanonicalInput> canonicalInputs(
            byte[] rawDiff,
            PrEnrichmentDataDto enrichment) {
        return canonicalInputs(rawDiff, enrichment, null);
    }

    private static List<CanonicalInput> canonicalInputs(
            byte[] rawDiff,
            PrEnrichmentDataDto enrichment,
            RagExecutionConfigV1 ragExecutionConfig) {
        Objects.requireNonNull(rawDiff, "rawDiff");
        List<CanonicalInput> inputs = new ArrayList<>();
        inputs.add(new CanonicalInput(
                ImmutableExecutionManifest.RAW_DIFF_CONTENT_KEY,
                ArtifactManifestEntry.Kind.RAW_DIFF,
                rawDiff));

        if (enrichment != null) {
            Set<String> sourcePaths = new HashSet<>();
            int enrichedCount = 0;
            int skippedCount = 0;
            long totalContentBytes = 0L;
            for (FileContentDto file : safeFileContents(enrichment)) {
                Objects.requireNonNull(file, "enrichment file content");
                String path = file.path();
                if (path == null || path.isBlank() || path.length() > 1024
                        || path.indexOf('\0') >= 0) {
                    throw new IllegalArgumentException(
                            "enrichment source path is invalid");
                }
                if (!sourcePaths.add(path)) {
                    throw new IllegalArgumentException(
                            "enrichment contains a duplicate source path");
                }
                if (file.skipped()) {
                    if (file.content() != null) {
                        throw new IllegalArgumentException(
                                "skipped enrichment source cannot carry content");
                    }
                    if (file.skipReason() == null || file.skipReason().isBlank()) {
                        throw new IllegalArgumentException(
                                "skipped enrichment source requires an explicit reason");
                    }
                    skippedCount++;
                    continue;
                }
                if (file.content() == null) {
                    throw new IllegalArgumentException(
                            "non-skipped enrichment source must carry content");
                }
                byte[] content = file.content().getBytes(StandardCharsets.UTF_8);
                if (file.sizeBytes() != content.length) {
                    throw new IllegalArgumentException(
                            "enrichment source byte length is not UTF-8 exact");
                }
                enrichedCount++;
                totalContentBytes = Math.addExact(
                        totalContentBytes, content.length);
                inputs.add(new CanonicalInput(
                        path,
                        ArtifactManifestEntry.Kind.SOURCE_FILE,
                        content));
            }
            PrEnrichmentDataDto.EnrichmentStats stats = enrichment.stats();
            if (stats == null
                    || stats.totalFilesRequested() != sourcePaths.size()
                    || stats.filesEnriched() != enrichedCount
                    || stats.filesSkipped() != skippedCount
                    || stats.totalContentSizeBytes() != totalContentBytes) {
                throw new IllegalArgumentException(
                        "enrichment source accounting is incomplete");
            }
            inputs.add(new CanonicalInput(
                    ImmutableExecutionManifest.PR_ENRICHMENT_CONTENT_KEY,
                    ArtifactManifestEntry.Kind.PR_ENRICHMENT,
                    canonicalEnrichmentBytes(enrichment)));
        }
        if (ragExecutionConfig != null) {
            inputs.add(new CanonicalInput(
                    ImmutableExecutionManifest.RAG_EXECUTION_CONFIG_CONTENT_KEY,
                    ArtifactManifestEntry.Kind.EXECUTION_CONFIG,
                    ragExecutionConfig.canonicalBytes()));
        }
        return List.copyOf(inputs);
    }

    private static void appendIdentityPart(StringBuilder target, String value) {
        target.append(value.length()).append(':').append(value).append('\n');
    }

    private record CanonicalInput(
            String contentKey,
            ArtifactManifestEntry.Kind kind,
            byte[] content) {
        private CanonicalInput {
            Objects.requireNonNull(contentKey, "contentKey");
            Objects.requireNonNull(kind, "kind");
            content = Objects.requireNonNull(content, "content").clone();
        }

        @Override
        public byte[] content() {
            return content.clone();
        }
    }

    private static ExecutionArtifactPayload payload(
            String executionId,
            String artifactId,
            String contentKey,
            String headSha,
            ArtifactManifestEntry.Kind kind,
            byte[] content,
            String artifactSchemaVersion,
            String producer,
            String producerVersion) {
        byte[] immutableContent = content.clone();
        ArtifactManifestEntry entry = new ArtifactManifestEntry(
                executionId,
                artifactId,
                contentKey,
                headSha,
                sha256(immutableContent),
                immutableContent.length,
                kind,
                artifactSchemaVersion,
                producer,
                producerVersion);
        return new ExecutionArtifactPayload(entry, immutableContent);
    }

    private static String deterministicArtifactId(
            String prefix,
            String executionId,
            String contentKey) {
        byte[] identity = (executionId + "\0" + contentKey)
                .getBytes(StandardCharsets.UTF_8);
        return prefix + ":" + sha256(identity);
    }

    private static String sha256(byte[] value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }
}
