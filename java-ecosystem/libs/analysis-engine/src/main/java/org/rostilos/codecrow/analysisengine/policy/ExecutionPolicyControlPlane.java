package org.rostilos.codecrow.analysisengine.policy;

import java.math.BigInteger;
import java.time.Clock;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Locale;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;

/** Deterministic selector that freezes one immutable plan at execution creation. */
public final class ExecutionPolicyControlPlane {
    public static final String LEGACY_POLICY_VERSION = "legacy-review-v1";

    private static final Pattern EXECUTION_ID = Pattern.compile("[A-Za-z0-9._:-]{1,128}");
    private static final BigInteger BUCKET_COUNT = BigInteger.valueOf(10_000L);

    private final Set<String> knownPolicyVersions;
    private final ExecutionControlStore store;
    private final Clock clock;

    public ExecutionPolicyControlPlane(
            Set<String> knownPolicyVersions,
            ExecutionControlStore store,
            Clock clock) {
        this.knownPolicyVersions = Set.copyOf(Objects.requireNonNull(
                knownPolicyVersions, "knownPolicyVersions"));
        this.store = Objects.requireNonNull(store, "store");
        this.clock = Objects.requireNonNull(clock, "clock");
        if (!this.knownPolicyVersions.contains(LEGACY_POLICY_VERSION)) {
            throw new IllegalArgumentException("known policies must include " + LEGACY_POLICY_VERSION);
        }
        this.knownPolicyVersions.forEach(this::validateKnownVersion);
    }

    /**
     * Pure preview of whether this snapshot selects the candidate as the
     * publish-capable primary path. It deliberately ignores stop-new-work:
     * admission remains store-first in {@link #freeze(String, StableRolloutKey,
     * ExecutionPolicyConfig)}, while callers may need exact immutable inputs to
     * derive the execution ID of work that is already frozen.
     */
    public static boolean selectsCandidatePrimary(
            StableRolloutKey stableRolloutKey,
            ExecutionPolicyConfig config) {
        Objects.requireNonNull(stableRolloutKey, "stableRolloutKey");
        Objects.requireNonNull(config, "config");
        return selectsCandidatePrimary(
                config, rolloutBucket(stableRolloutKey, config));
    }

    /**
     * Returns the first durably selected plan for an identity. This store-first
     * check is intentional: a later flag refresh cannot rewrite an execution that
     * already exists.
     */
    public FrozenExecutionPlan freeze(
            String executionId,
            StableRolloutKey stableRolloutKey,
            ExecutionPolicyConfig config) {
        validateExecutionId(executionId);
        Objects.requireNonNull(stableRolloutKey, "stableRolloutKey");
        Objects.requireNonNull(config, "config");

        Optional<FrozenExecutionPlan> frozen = store.findPlan(executionId);
        if (frozen.isPresent()) {
            if (!executionId.equals(frozen.get().executionId())) {
                throw new IllegalStateException("execution control store returned the wrong plan identity");
            }
            return frozen.get();
        }
        if (config.stopNewWork()) {
            throw new NewWorkDisabledException(config.configRevision());
        }
        if (!knownPolicyVersions.contains(config.candidatePolicyVersion())) {
            throw new UnknownExecutionPolicyVersionException(config.candidatePolicyVersion());
        }

        // PostgreSQL and the cross-language canonical manifest contract retain
        // microsecond precision. Freeze that precision once so restart reads do
        // not alter the execution identity or its manifest digest.
        Instant createdAt = clock.instant().truncatedTo(ChronoUnit.MICROS);
        int rolloutBucket = rolloutBucket(stableRolloutKey, config);
        String stableKeyHash = sha256(stableRolloutKey.canonicalValue());
        PolicyExecution primary;
        PolicyExecution shadow = null;

        if (config.candidateKillSwitch() && config.mode() != ExecutionMode.LEGACY) {
            primary = primary(
                    executionId,
                    LEGACY_POLICY_VERSION,
                    ExecutionMode.LEGACY,
                    PolicySelectionReason.CANDIDATE_KILL_SWITCH_ROLLBACK,
                    rolloutBucket,
                    createdAt);
        } else {
            primary = switch (config.mode()) {
                case LEGACY -> primary(
                        executionId,
                        LEGACY_POLICY_VERSION,
                        ExecutionMode.LEGACY,
                        PolicySelectionReason.LEGACY_CONFIGURED,
                        rolloutBucket,
                        createdAt);
                case SHADOW -> {
                    shadow = new PolicyExecution(
                            executionId + ":shadow",
                            config.candidatePolicyVersion(),
                            ExecutionMode.SHADOW,
                            PolicySelectionReason.SHADOW_CANDIDATE,
                            rolloutBucket,
                            false,
                            createdAt);
                    yield primary(
                            executionId,
                            LEGACY_POLICY_VERSION,
                            ExecutionMode.LEGACY,
                            PolicySelectionReason.SHADOW_LEGACY_PRIMARY,
                            rolloutBucket,
                            createdAt);
                }
                case ACTIVE -> {
                    boolean selected = selectsCandidatePrimary(config, rolloutBucket);
                    yield selected
                            ? primary(
                                    executionId,
                                    config.candidatePolicyVersion(),
                                    ExecutionMode.ACTIVE,
                                    PolicySelectionReason.ACTIVE_ROLLOUT_SELECTED,
                                    rolloutBucket,
                                    createdAt)
                            : primary(
                                    executionId,
                                    LEGACY_POLICY_VERSION,
                                    ExecutionMode.LEGACY,
                                    PolicySelectionReason.ACTIVE_ROLLOUT_NOT_SELECTED,
                                    rolloutBucket,
                                    createdAt);
                }
            };
        }

        FrozenExecutionPlan selected = new FrozenExecutionPlan(
                executionId,
                config.configRevision(),
                stableKeyHash,
                primary,
                shadow,
                createdAt);
        FrozenExecutionPlan persisted = store.createPlanIfAbsent(selected);
        if (!executionId.equals(persisted.executionId())) {
            throw new IllegalStateException("execution control store claimed the wrong plan identity");
        }
        return persisted;
    }

    private PolicyExecution primary(
            String executionId,
            String policyVersion,
            ExecutionMode mode,
            PolicySelectionReason reason,
            int rolloutBucket,
            Instant createdAt) {
        return new PolicyExecution(
                executionId,
                policyVersion,
                mode,
                reason,
                rolloutBucket,
                true,
                createdAt);
    }

    private static boolean selectsCandidatePrimary(
            ExecutionPolicyConfig config,
            int rolloutBucket) {
        return config.mode() == ExecutionMode.ACTIVE
                && !config.candidateKillSwitch()
                && rolloutBucket < config.rolloutBasisPoints();
    }

    private static int rolloutBucket(
            StableRolloutKey stableRolloutKey,
            ExecutionPolicyConfig config) {
        String input = config.rolloutSalt()
                + '\0' + config.candidatePolicyVersion()
                + '\0' + stableRolloutKey.canonicalValue();
        byte[] digest = digest(input);
        return new BigInteger(1, digest).mod(BUCKET_COUNT).intValueExact();
    }

    private String sha256(String value) {
        return PolicyHashing.sha256(value);
    }

    private static byte[] digest(String value) {
        return java.util.HexFormat.of().parseHex(PolicyHashing.sha256(value));
    }

    private void validateExecutionId(String executionId) {
        if (executionId == null || !EXECUTION_ID.matcher(executionId).matches()) {
            throw new IllegalArgumentException("executionId is invalid");
        }
    }

    private void validateKnownVersion(String policyVersion) {
        if (!policyVersion.equals(policyVersion.toLowerCase(Locale.ROOT))
                || !policyVersion.matches("[a-z0-9][a-z0-9._-]{0,63}")) {
            throw new IllegalArgumentException("known policy version is invalid");
        }
    }
}
