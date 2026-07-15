package org.rostilos.codecrow.analysisengine.policy;

import java.util.Objects;
import java.util.regex.Pattern;

/** One immutable read of all behavior-affecting rollout flags. */
public record ExecutionPolicyConfig(
        String configRevision,
        ExecutionMode mode,
        String candidatePolicyVersion,
        int rolloutBasisPoints,
        String rolloutSalt,
        boolean stopNewWork,
        boolean candidateKillSwitch) {
    private static final Pattern REVISION = Pattern.compile("[A-Za-z0-9._:-]{1,128}");
    private static final Pattern POLICY_VERSION = Pattern.compile("[a-z0-9][a-z0-9._-]{0,63}");

    public ExecutionPolicyConfig {
        Objects.requireNonNull(mode, "mode");
        if (configRevision == null || !REVISION.matcher(configRevision).matches()) {
            throw new IllegalArgumentException("configRevision is invalid");
        }
        if (candidatePolicyVersion == null
                || !POLICY_VERSION.matcher(candidatePolicyVersion).matches()) {
            throw new IllegalArgumentException("candidatePolicyVersion is invalid");
        }
        if (rolloutBasisPoints < 0 || rolloutBasisPoints > 10_000) {
            throw new IllegalArgumentException("rolloutBasisPoints must be between 0 and 10000");
        }
        if (rolloutSalt == null || rolloutSalt.isBlank() || rolloutSalt.length() > 128
                || rolloutSalt.chars().anyMatch(Character::isISOControl)) {
            throw new IllegalArgumentException("rolloutSalt is invalid");
        }
    }
}
