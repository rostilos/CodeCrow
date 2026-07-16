package org.rostilos.codecrow.analysisengine.policy;

import java.util.Locale;
import java.util.regex.Pattern;

/** Stable delivery identity; it accepts provider/revision identity, never content. */
public record PublicationKey(
        String provider,
        long projectId,
        long pullRequestId,
        String headRevision) {
    private static final Pattern PROVIDER = Pattern.compile("[a-z0-9_-]{1,32}");
    private static final Pattern REVISION = Pattern.compile("[0-9a-f]{40,64}");

    public PublicationKey {
        if (provider == null) {
            throw new IllegalArgumentException("provider is required");
        }
        provider = provider.toLowerCase(Locale.ROOT);
        if (!PROVIDER.matcher(provider).matches()) {
            throw new IllegalArgumentException("provider is invalid");
        }
        if (projectId <= 0 || pullRequestId <= 0) {
            throw new IllegalArgumentException("projectId and pullRequestId must be positive");
        }
        if (headRevision == null || !REVISION.matcher(headRevision).matches()) {
            throw new IllegalArgumentException("headRevision must be a lowercase hexadecimal revision");
        }
    }

    public static PublicationKey forPullRequest(
            String provider,
            long projectId,
            long pullRequestId,
            String headRevision) {
        return new PublicationKey(provider, projectId, pullRequestId, headRevision);
    }

    String canonicalValue() {
        return scopeCanonicalValue() + ':' + headRevision;
    }

    String scopeCanonicalValue() {
        return provider + ':' + projectId + ':' + pullRequestId;
    }
}
