package org.rostilos.codecrow.analysisengine.dto.request.ai;

import java.util.Objects;
import java.util.regex.Pattern;

/** Coordinates for an exact-head repository archive staged for AGENTIC review. */
public record AgenticRepositoryArchive(
        String workspaceKey,
        String snapshotSha,
        String contentDigest,
        long byteLength
) {
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");
    private static final Pattern EXACT_REVISION =
            Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");

    public AgenticRepositoryArchive {
        requireMatch(workspaceKey, SHA_256, "workspaceKey");
        requireMatch(snapshotSha, EXACT_REVISION, "snapshotSha");
        requireMatch(contentDigest, SHA_256, "contentDigest");
        if (byteLength <= 0) {
            throw new IllegalArgumentException("byteLength must be positive");
        }
    }

    private static void requireMatch(String value, Pattern pattern, String name) {
        Objects.requireNonNull(value, name);
        if (!pattern.matcher(value).matches()) {
            throw new IllegalArgumentException(name + " has an invalid format");
        }
    }
}
