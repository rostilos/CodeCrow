package org.rostilos.codecrow.analysisengine.delivery;

import java.util.Locale;
import java.util.regex.Pattern;

/** Durable latest-head identity used to admit an outbox intent transactionally. */
public record ReviewDeliveryHead(
        String provider,
        long tenantId,
        long projectId,
        String repositoryId,
        long pullRequestId,
        String executionId,
        String headRevision,
        long generation) {
    private static final Pattern PROVIDER = Pattern.compile("[a-z0-9_-]{1,32}");
    private static final Pattern IDENTIFIER =
            Pattern.compile("[A-Za-z0-9][A-Za-z0-9._:-]{0,159}");
    private static final Pattern REVISION =
            Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");

    public ReviewDeliveryHead {
        if (provider == null) {
            throw new IllegalArgumentException("provider is invalid");
        }
        provider = provider.toLowerCase(Locale.ROOT);
        requireMatch(provider, PROVIDER, "provider");
        if (tenantId <= 0 || projectId <= 0 || pullRequestId <= 0) {
            throw new IllegalArgumentException(
                    "tenantId, projectId, and pullRequestId must be positive");
        }
        requireText(repositoryId, "repositoryId", 512);
        requireMatch(executionId, IDENTIFIER, "executionId");
        if (headRevision == null) {
            throw new IllegalArgumentException("headRevision is invalid");
        }
        headRevision = headRevision.toLowerCase(Locale.ROOT);
        requireMatch(headRevision, REVISION, "headRevision");
        if (generation <= 0) {
            throw new IllegalArgumentException("generation must be positive");
        }
    }

    boolean samePullRequestCoordinate(ReviewDeliveryHead other) {
        return other != null
                && provider.equals(other.provider)
                && tenantId == other.tenantId
                && projectId == other.projectId
                && repositoryId.equals(other.repositoryId)
                && pullRequestId == other.pullRequestId;
    }

    private static void requireMatch(
            String value,
            Pattern pattern,
            String field) {
        if (value == null || !pattern.matcher(value).matches()) {
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
