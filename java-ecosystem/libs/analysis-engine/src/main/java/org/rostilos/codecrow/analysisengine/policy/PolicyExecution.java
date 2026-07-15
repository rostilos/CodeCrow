package org.rostilos.codecrow.analysisengine.policy;

import java.time.Instant;
import java.util.Objects;
import java.util.regex.Pattern;

/** Immutable policy and capability snapshot for exactly one execution path. */
public record PolicyExecution(
        String executionId,
        String policyVersion,
        ExecutionMode mode,
        PolicySelectionReason selectionReason,
        int rolloutBucket,
        boolean publicationAllowed,
        Instant createdAt) {
    private static final Pattern IDENTIFIER = Pattern.compile("[A-Za-z0-9._:-]{1,160}");
    private static final Pattern POLICY_VERSION = Pattern.compile("[a-z0-9][a-z0-9._-]{0,63}");

    public PolicyExecution {
        if (executionId == null || !IDENTIFIER.matcher(executionId).matches()) {
            throw new IllegalArgumentException("executionId is invalid");
        }
        if (policyVersion == null || !POLICY_VERSION.matcher(policyVersion).matches()) {
            throw new IllegalArgumentException("policyVersion is invalid");
        }
        Objects.requireNonNull(mode, "mode");
        Objects.requireNonNull(selectionReason, "selectionReason");
        Objects.requireNonNull(createdAt, "createdAt");
        if (rolloutBucket < 0 || rolloutBucket >= 10_000) {
            throw new IllegalArgumentException("rolloutBucket must be between 0 and 9999");
        }
        if (mode == ExecutionMode.SHADOW && publicationAllowed) {
            throw new IllegalArgumentException("shadow execution cannot have publication capability");
        }
        if (mode != ExecutionMode.SHADOW && !publicationAllowed) {
            throw new IllegalArgumentException("primary execution must retain publication capability");
        }
    }

    public boolean candidatePath() {
        return mode == ExecutionMode.ACTIVE || mode == ExecutionMode.SHADOW;
    }
}
