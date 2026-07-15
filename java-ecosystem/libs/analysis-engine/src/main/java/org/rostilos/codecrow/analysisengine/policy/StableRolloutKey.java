package org.rostilos.codecrow.analysisengine.policy;

/**
 * Stable rollout identity. The deliberately narrow factory prevents callers from
 * selecting policy with filenames, source contents, benchmark labels, or issue
 * outcomes.
 */
public record StableRolloutKey(long workspaceId, long projectId) {
    public StableRolloutKey {
        if (workspaceId <= 0 || projectId <= 0) {
            throw new IllegalArgumentException("workspaceId and projectId must be positive");
        }
    }

    public static StableRolloutKey forProject(long workspaceId, long projectId) {
        return new StableRolloutKey(workspaceId, projectId);
    }

    String canonicalValue() {
        return "workspace:" + workspaceId + ":project:" + projectId;
    }
}
