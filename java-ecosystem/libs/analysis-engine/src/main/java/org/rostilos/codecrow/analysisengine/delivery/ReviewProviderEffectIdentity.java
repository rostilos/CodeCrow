package org.rostilos.codecrow.analysisengine.delivery;

import java.util.Locale;
import java.util.regex.Pattern;

/** Canonical identity for the single provider-visible effect of one report. */
public final class ReviewProviderEffectIdentity {
    private static final Pattern PROVIDER = Pattern.compile("[a-z0-9_-]{1,32}");
    private static final Pattern REVISION =
            Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern PUBLICATION_KIND =
            Pattern.compile("[A-Z][A-Z0-9_]{0,63}");

    private ReviewProviderEffectIdentity() {
    }

    public static String derive(
            long tenantId,
            String provider,
            String repositoryId,
            long pullRequestId,
            String headRevision,
            String reportDigest,
            String publicationKind) {
        if (tenantId <= 0 || pullRequestId <= 0) {
            throw new IllegalArgumentException(
                    "tenantId and pullRequestId must be positive");
        }
        String normalizedProvider = lower(provider, "provider");
        requireMatch(normalizedProvider, PROVIDER, "provider");
        requireText(repositoryId, "repositoryId", 512);
        String normalizedHead = lower(headRevision, "headRevision");
        requireMatch(normalizedHead, REVISION, "headRevision");
        String normalizedReportDigest = lower(reportDigest, "reportDigest");
        requireMatch(normalizedReportDigest, SHA_256, "reportDigest");
        String normalizedPublicationKind = upper(
                publicationKind, "publicationKind");
        requireMatch(
                normalizedPublicationKind,
                PUBLICATION_KIND,
                "publicationKind");

        return ReviewDeliveryTruth.stableId(
                "review-provider-effect-v1",
                Long.toString(tenantId),
                normalizedProvider,
                repositoryId,
                Long.toString(pullRequestId),
                normalizedHead,
                normalizedReportDigest,
                normalizedPublicationKind);
    }

    private static String lower(String value, String field) {
        if (value == null) {
            throw new IllegalArgumentException(field + " is invalid");
        }
        return value.toLowerCase(Locale.ROOT);
    }

    private static String upper(String value, String field) {
        if (value == null) {
            throw new IllegalArgumentException(field + " is invalid");
        }
        return value.toUpperCase(Locale.ROOT);
    }

    private static void requireMatch(
            String value,
            Pattern pattern,
            String field) {
        if (!pattern.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " is invalid");
        }
    }

    private static void requireText(String value, String field, int maxLength) {
        if (value == null
                || value.isBlank()
                || value.length() > maxLength
                || value.indexOf('\0') >= 0
                || !value.equals(value.trim())) {
            throw new IllegalArgumentException(field + " is invalid");
        }
    }
}
