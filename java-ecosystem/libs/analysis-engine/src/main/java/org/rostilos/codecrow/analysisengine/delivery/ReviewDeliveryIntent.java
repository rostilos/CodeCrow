package org.rostilos.codecrow.analysisengine.delivery;

import java.util.Locale;
import java.util.regex.Pattern;

/** Immutable identity and truth binding for one provider delivery. */
public record ReviewDeliveryIntent(
        String intentId,
        String executionId,
        String artifactManifestDigest,
        String snapshotRevision,
        long headGeneration,
        String reportArtifactId,
        String reportDigest,
        String analysisTruthDigest,
        String provider,
        long projectId,
        long pullRequestId,
        String publicationKind,
        String idempotencyKey) {
    private static final Pattern IDENTIFIER =
            Pattern.compile("[A-Za-z0-9][A-Za-z0-9._:-]{0,159}");
    private static final Pattern REVISION =
            Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern REPORT_ARTIFACT =
            Pattern.compile("review-output:[0-9a-f]{64}");
    private static final Pattern PROVIDER = Pattern.compile("[a-z0-9_-]{1,32}");
    private static final Pattern PUBLICATION_KIND =
            Pattern.compile("[A-Z][A-Z0-9_]{0,63}");

    public ReviewDeliveryIntent {
        requireMatch(intentId, IDENTIFIER, "intentId");
        requireMatch(executionId, IDENTIFIER, "executionId");
        requireMatch(
                artifactManifestDigest,
                SHA_256,
                "artifactManifestDigest");
        requireMatch(snapshotRevision, REVISION, "snapshotRevision");
        if (headGeneration <= 0) {
            throw new IllegalArgumentException("headGeneration must be positive");
        }
        requireMatch(reportArtifactId, REPORT_ARTIFACT, "reportArtifactId");
        requireMatch(reportDigest, SHA_256, "reportDigest");
        requireMatch(analysisTruthDigest, SHA_256, "analysisTruthDigest");
        if (provider == null) {
            throw new IllegalArgumentException("provider is invalid");
        }
        provider = provider.toLowerCase(Locale.ROOT);
        requireMatch(provider, PROVIDER, "provider");
        if (projectId <= 0 || pullRequestId <= 0) {
            throw new IllegalArgumentException(
                    "projectId and pullRequestId must be positive");
        }
        requireMatch(publicationKind, PUBLICATION_KIND, "publicationKind");
        requireMatch(idempotencyKey, SHA_256, "idempotencyKey");
    }

    private static void requireMatch(
            String value,
            Pattern pattern,
            String field) {
        if (value == null || !pattern.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " is invalid");
        }
    }
}
