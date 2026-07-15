package org.rostilos.codecrow.analysisengine.policy;

import java.time.Instant;
import java.util.Objects;
import java.util.regex.Pattern;

/**
 * The atomic rollout decision. A shadow plan always keeps legacy as the primary
 * publication path and carries the candidate as a separate non-publishing path.
 */
public record FrozenExecutionPlan(
        String executionId,
        String configRevision,
        String stableRolloutKeyHash,
        PolicyExecution primary,
        PolicyExecution shadow,
        Instant createdAt) {
    private static final Pattern SHA_256 = Pattern.compile("[0-9a-f]{64}");

    public FrozenExecutionPlan {
        Objects.requireNonNull(primary, "primary");
        Objects.requireNonNull(createdAt, "createdAt");
        if (!primary.executionId().equals(executionId)) {
            throw new IllegalArgumentException("primary execution identity must match the plan");
        }
        if (primary.mode() == ExecutionMode.SHADOW) {
            throw new IllegalArgumentException("primary path must be publish-capable");
        }
        if (configRevision == null || configRevision.isBlank()) {
            throw new IllegalArgumentException("configRevision is required");
        }
        if (stableRolloutKeyHash == null
                || !SHA_256.matcher(stableRolloutKeyHash).matches()) {
            throw new IllegalArgumentException("stableRolloutKeyHash must be SHA-256");
        }
        if (shadow != null) {
            if (shadow.mode() != ExecutionMode.SHADOW) {
                throw new IllegalArgumentException("shadow path must be non-publishing");
            }
            if (!shadow.executionId().equals(executionId + ":shadow")) {
                throw new IllegalArgumentException("shadow identity must be derived from the plan");
            }
        }
    }
}
