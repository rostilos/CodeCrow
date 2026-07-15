package org.rostilos.codecrow.analysisengine.policy;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Constructor;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ExecutionPolicyValidationTest {
    private static final Instant NOW = Instant.parse("2026-07-14T12:00:00Z");
    private static final Clock CLOCK = Clock.fixed(NOW, ZoneOffset.UTC);
    private static final String HASH = "a".repeat(64);

    @Test
    void hashingIsStableAndFailsClosedWhenTheAlgorithmIsUnavailable() throws Exception {
        assertThat(PolicyHashing.sha256("policy-input"))
                .isEqualTo(PolicyHashing.sha256("policy-input"))
                .hasSize(64);
        assertThatThrownBy(() -> PolicyHashing.digestHex("missing-digest", "value"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("missing-digest is unavailable");

        Constructor<PolicyHashing> constructor = PolicyHashing.class.getDeclaredConstructor();
        constructor.setAccessible(true);
        assertThat(constructor.newInstance()).isNotNull();
    }

    @Test
    void stableRolloutKeyRequiresPositiveWorkspaceAndProjectIdentities() {
        assertThatThrownBy(() -> StableRolloutKey.forProject(0, 1))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> StableRolloutKey.forProject(1, 0))
                .isInstanceOf(IllegalArgumentException.class);

        assertThat(StableRolloutKey.forProject(7, 41).canonicalValue())
                .isEqualTo("workspace:7:project:41");
    }

    @Test
    void policyConfigurationRejectsEveryMalformedBoundary() {
        assertInvalidConfig(null, ExecutionMode.LEGACY, "candidate-review-v2", 0, "salt");
        assertInvalidConfig("bad revision", ExecutionMode.LEGACY, "candidate-review-v2", 0, "salt");
        assertThatThrownBy(() -> config("revision", null, "candidate-review-v2", 0, "salt"))
                .isInstanceOf(NullPointerException.class);
        assertInvalidConfig("revision", ExecutionMode.LEGACY, null, 0, "salt");
        assertInvalidConfig("revision", ExecutionMode.LEGACY, "Candidate", 0, "salt");
        assertInvalidConfig("revision", ExecutionMode.LEGACY, "candidate", -1, "salt");
        assertInvalidConfig("revision", ExecutionMode.LEGACY, "candidate", 10_001, "salt");
        assertInvalidConfig("revision", ExecutionMode.LEGACY, "candidate", 0, null);
        assertInvalidConfig("revision", ExecutionMode.LEGACY, "candidate", 0, "   ");
        assertInvalidConfig("revision", ExecutionMode.LEGACY, "candidate", 0, "s".repeat(129));
        assertInvalidConfig("revision", ExecutionMode.LEGACY, "candidate", 0, "salt\nvalue");

        assertThat(config("revision", ExecutionMode.LEGACY, "candidate", 10_000, "salt"))
                .extracting(ExecutionPolicyConfig::rolloutBasisPoints)
                .isEqualTo(10_000);
    }

    @Test
    void policyExecutionEnforcesIdentityCapabilityAndRolloutBounds() {
        assertInvalidExecution(null, "legacy-review-v1", ExecutionMode.LEGACY, 0, true);
        assertInvalidExecution("bad identity", "legacy-review-v1", ExecutionMode.LEGACY, 0, true);
        assertInvalidExecution("execution", null, ExecutionMode.LEGACY, 0, true);
        assertInvalidExecution("execution", "Legacy", ExecutionMode.LEGACY, 0, true);
        assertThatThrownBy(() -> new PolicyExecution(
                "execution", "legacy-review-v1", null,
                PolicySelectionReason.LEGACY_CONFIGURED, 0, true, NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new PolicyExecution(
                "execution", "legacy-review-v1", ExecutionMode.LEGACY,
                null, 0, true, NOW))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new PolicyExecution(
                "execution", "legacy-review-v1", ExecutionMode.LEGACY,
                PolicySelectionReason.LEGACY_CONFIGURED, 0, true, null))
                .isInstanceOf(NullPointerException.class);
        assertInvalidExecution("execution", "legacy-review-v1", ExecutionMode.LEGACY, -1, true);
        assertInvalidExecution("execution", "legacy-review-v1", ExecutionMode.LEGACY, 10_000, true);
        assertInvalidExecution("execution", "candidate-review-v2", ExecutionMode.SHADOW, 0, true);
        assertInvalidExecution("execution", "legacy-review-v1", ExecutionMode.LEGACY, 0, false);

        PolicyExecution legacy = execution("legacy", ExecutionMode.LEGACY, true);
        PolicyExecution active = execution("active", ExecutionMode.ACTIVE, true);
        PolicyExecution shadow = execution("shadow", ExecutionMode.SHADOW, false);
        assertThat(legacy.candidatePath()).isFalse();
        assertThat(active.candidatePath()).isTrue();
        assertThat(shadow.candidatePath()).isTrue();
    }

    @Test
    void frozenPlanRejectsMismatchedCapabilitiesAndIdentities() {
        PolicyExecution primary = execution("plan", ExecutionMode.LEGACY, true);
        PolicyExecution shadow = execution("plan:shadow", ExecutionMode.SHADOW, false);

        assertThatThrownBy(() -> plan("plan", execution("other", ExecutionMode.LEGACY, true), null,
                "revision", HASH)).hasMessageContaining("primary execution identity");
        assertThatThrownBy(() -> plan("plan", execution("plan", ExecutionMode.SHADOW, false), null,
                "revision", HASH)).hasMessageContaining("primary path");
        assertThatThrownBy(() -> plan("plan", primary, null, null, HASH))
                .hasMessageContaining("configRevision");
        assertThatThrownBy(() -> plan("plan", primary, null, "  ", HASH))
                .hasMessageContaining("configRevision");
        assertThatThrownBy(() -> plan("plan", primary, null, "revision", null))
                .hasMessageContaining("SHA-256");
        assertThatThrownBy(() -> plan("plan", primary, null, "revision", "not-a-hash"))
                .hasMessageContaining("SHA-256");
        assertThatThrownBy(() -> plan("plan", primary,
                execution("plan:shadow", ExecutionMode.ACTIVE, true), "revision", HASH))
                .hasMessageContaining("shadow path");
        assertThatThrownBy(() -> plan("plan",
                primary, execution("different:shadow", ExecutionMode.SHADOW, false), "revision", HASH))
                .hasMessageContaining("shadow identity");

        assertThat(plan("plan", primary, null, "revision", HASH).shadow()).isNull();
        assertThat(plan("plan", primary, shadow, "revision", HASH).shadow()).isEqualTo(shadow);
    }

    @Test
    void artifactsAndPublicationKeysEnforceBoundedStableIdentifiers() {
        assertInvalidArtifact(null, ArtifactNamespace.PRIMARY, "artifact", "{}");
        assertInvalidArtifact("bad identity", ArtifactNamespace.PRIMARY, "artifact", "{}");
        assertThatThrownBy(() -> artifact("execution", null, "artifact", "{}"))
                .isInstanceOf(NullPointerException.class);
        assertInvalidArtifact("execution", ArtifactNamespace.PRIMARY, null, "{}");
        assertInvalidArtifact("execution", ArtifactNamespace.PRIMARY, "bad artifact", "{}");
        assertThatThrownBy(() -> artifact("execution", ArtifactNamespace.PRIMARY, "artifact", null))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> new ExecutionArtifact(
                "execution", ArtifactNamespace.PRIMARY, "artifact", "{}", null))
                .isInstanceOf(NullPointerException.class);
        assertInvalidArtifact(
                "execution", ArtifactNamespace.PRIMARY, "artifact", "x".repeat(4 * 1024 * 1024 + 1));

        assertThatThrownBy(() -> publication(null, 1, 1, "a".repeat(40)))
                .hasMessageContaining("provider is required");
        assertThatThrownBy(() -> publication("bad provider", 1, 1, "a".repeat(40)))
                .hasMessageContaining("provider is invalid");
        assertThatThrownBy(() -> publication("github", 0, 1, "a".repeat(40)))
                .hasMessageContaining("must be positive");
        assertThatThrownBy(() -> publication("github", 1, 0, "a".repeat(40)))
                .hasMessageContaining("must be positive");
        assertThatThrownBy(() -> publication("github", 1, 1, null))
                .hasMessageContaining("lowercase hexadecimal");
        assertThatThrownBy(() -> publication("github", 1, 1, "A".repeat(40)))
                .hasMessageContaining("lowercase hexadecimal");

        PublicationKey key = publication("GitHub", 7, 41, "b".repeat(40));
        assertThat(key.provider()).isEqualTo("github");
        assertThat(key.canonicalValue()).isEqualTo("github:7:41:" + "b".repeat(40));
    }

    @Test
    void lifecycleTransitionsAreExplicitAndTerminalStatesStayTerminal() {
        ExecutionPolicyConfig noSwitch = config(
                "revision", ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, "salt");
        ExecutionPolicyConfig candidateSwitch = new ExecutionPolicyConfig(
                "revision", ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, "salt", false, true);
        ExecutionPolicyConfig globalStop = new ExecutionPolicyConfig(
                "revision", ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, "salt", true, false);

        ExecutionLifecycle created = lifecycle("created", ExecutionMode.LEGACY);
        assertThat(created.execution().executionId()).isEqualTo("created");
        assertThat(created.complete()).isTrue();
        assertThat(created.complete()).isTrue();
        assertThat(created.start()).isFalse();
        assertThat(created.fail()).isFalse();
        assertThat(created.reconcileKillSwitch(globalStop)).isFalse();

        ExecutionLifecycle running = lifecycle("running", ExecutionMode.ACTIVE);
        assertThat(running.start()).isTrue();
        assertThat(running.start()).isFalse();
        assertThat(running.complete()).isTrue();

        ExecutionLifecycle failed = lifecycle("failed", ExecutionMode.ACTIVE);
        assertThat(failed.fail()).isTrue();
        assertThat(failed.fail()).isFalse();
        assertThat(failed.complete()).isFalse();
        assertThat(failed.reconcileKillSwitch(globalStop)).isFalse();

        ExecutionLifecycle cancelled = lifecycle("cancelled", ExecutionMode.ACTIVE);
        assertThat(cancelled.markCancelled()).isFalse();
        assertThat(cancelled.reconcileKillSwitch(globalStop)).isTrue();
        assertThat(cancelled.reconcileKillSwitch(globalStop)).isFalse();
        assertThat(cancelled.markCancelled()).isTrue();
        assertThat(cancelled.fail()).isFalse();
        assertThat(cancelled.complete()).isFalse();
        assertThat(cancelled.reconcileKillSwitch(globalStop)).isFalse();

        ExecutionLifecycle legacy = lifecycle("legacy", ExecutionMode.LEGACY);
        assertThat(legacy.reconcileKillSwitch(noSwitch)).isFalse();
        assertThat(legacy.reconcileKillSwitch(candidateSwitch)).isFalse();
        assertThat(legacy.state()).isEqualTo(ExecutionLifecycleState.CREATED);

        ExecutionLifecycle candidate = lifecycle("candidate", ExecutionMode.ACTIVE);
        assertThat(candidate.reconcileKillSwitch(candidateSwitch)).isTrue();
        assertThat(candidate.state()).isEqualTo(ExecutionLifecycleState.CANCELLATION_REQUESTED);
    }

    @Test
    void controlPlaneFailsClosedForMalformedVersionsAndWrongStoreIdentities() {
        assertThatThrownBy(() -> new ExecutionPolicyControlPlane(
                Set.of("candidate-review-v2"), new ConfigurableStore(), CLOCK))
                .hasMessageContaining("must include legacy-review-v1");
        assertThatThrownBy(() -> new ExecutionPolicyControlPlane(
                Set.of("legacy-review-v1", "Candidate"), new ConfigurableStore(), CLOCK))
                .hasMessageContaining("known policy version is invalid");
        assertThatThrownBy(() -> new ExecutionPolicyControlPlane(
                Set.of("legacy-review-v1", "bad version"), new ConfigurableStore(), CLOCK))
                .hasMessageContaining("known policy version is invalid");

        ConfigurableStore store = new ConfigurableStore();
        ExecutionPolicyControlPlane controlPlane = controlPlane(store);
        ExecutionPolicyConfig legacyWithCandidateKill = new ExecutionPolicyConfig(
                "revision", ExecutionMode.LEGACY, "candidate-review-v2", 10_000,
                "salt", false, true);
        FrozenExecutionPlan legacy = controlPlane.freeze(
                "legacy-kill", StableRolloutKey.forProject(1, 1), legacyWithCandidateKill);
        assertThat(legacy.primary().selectionReason())
                .isEqualTo(PolicySelectionReason.LEGACY_CONFIGURED);

        FrozenExecutionPlan notSelected = controlPlane.freeze(
                "not-selected",
                StableRolloutKey.forProject(1, 2),
                new ExecutionPolicyConfig(
                        "revision", ExecutionMode.ACTIVE, "candidate-review-v2", 0,
                        "salt", false, false));
        assertThat(notSelected.primary().selectionReason())
                .isEqualTo(PolicySelectionReason.ACTIVE_ROLLOUT_NOT_SELECTED);

        assertThatThrownBy(() -> controlPlane.freeze(
                null, StableRolloutKey.forProject(1, 1), legacyWithCandidateKill))
                .hasMessageContaining("executionId is invalid");
        assertThatThrownBy(() -> controlPlane.freeze(
                "bad identity", StableRolloutKey.forProject(1, 1), legacyWithCandidateKill))
                .hasMessageContaining("executionId is invalid");
        assertThatThrownBy(() -> controlPlane.freeze("null-key", null, legacyWithCandidateKill))
                .isInstanceOf(NullPointerException.class);
        assertThatThrownBy(() -> controlPlane.freeze(
                "null-config", StableRolloutKey.forProject(1, 1), null))
                .isInstanceOf(NullPointerException.class);

        store.found = Optional.of(validPlan("other"));
        assertThatThrownBy(() -> controlPlane.freeze(
                "requested", StableRolloutKey.forProject(1, 1), legacyWithCandidateKill))
                .hasMessageContaining("wrong plan identity");

        store.found = Optional.empty();
        store.created = validPlan("other");
        assertThatThrownBy(() -> controlPlane.freeze(
                "claimed", StableRolloutKey.forProject(1, 1), legacyWithCandidateKill))
                .hasMessageContaining("claimed the wrong plan identity");
    }

    private static void assertInvalidConfig(
            String revision,
            ExecutionMode mode,
            String candidate,
            int basisPoints,
            String salt) {
        assertThatThrownBy(() -> config(revision, mode, candidate, basisPoints, salt))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private static ExecutionPolicyConfig config(
            String revision,
            ExecutionMode mode,
            String candidate,
            int basisPoints,
            String salt) {
        return new ExecutionPolicyConfig(
                revision, mode, candidate, basisPoints, salt, false, false);
    }

    private static void assertInvalidExecution(
            String executionId,
            String policyVersion,
            ExecutionMode mode,
            int bucket,
            boolean publicationAllowed) {
        assertThatThrownBy(() -> new PolicyExecution(
                executionId,
                policyVersion,
                mode,
                PolicySelectionReason.LEGACY_CONFIGURED,
                bucket,
                publicationAllowed,
                NOW)).isInstanceOf(IllegalArgumentException.class);
    }

    private static PolicyExecution execution(
            String executionId,
            ExecutionMode mode,
            boolean publicationAllowed) {
        return new PolicyExecution(
                executionId,
                mode == ExecutionMode.LEGACY ? "legacy-review-v1" : "candidate-review-v2",
                mode,
                mode == ExecutionMode.SHADOW
                        ? PolicySelectionReason.SHADOW_CANDIDATE
                        : PolicySelectionReason.LEGACY_CONFIGURED,
                0,
                publicationAllowed,
                NOW);
    }

    private static FrozenExecutionPlan plan(
            String executionId,
            PolicyExecution primary,
            PolicyExecution shadow,
            String revision,
            String hash) {
        return new FrozenExecutionPlan(executionId, revision, hash, primary, shadow, NOW);
    }

    private static FrozenExecutionPlan validPlan(String executionId) {
        return plan(executionId, execution(executionId, ExecutionMode.LEGACY, true),
                null, "revision", HASH);
    }

    private static ExecutionArtifact artifact(
            String executionId,
            ArtifactNamespace namespace,
            String artifactId,
            String payload) {
        return new ExecutionArtifact(executionId, namespace, artifactId, payload, NOW);
    }

    private static void assertInvalidArtifact(
            String executionId,
            ArtifactNamespace namespace,
            String artifactId,
            String payload) {
        assertThatThrownBy(() -> artifact(executionId, namespace, artifactId, payload))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private static PublicationKey publication(
            String provider,
            long projectId,
            long pullRequestId,
            String revision) {
        return PublicationKey.forPullRequest(provider, projectId, pullRequestId, revision);
    }

    private static ExecutionLifecycle lifecycle(String executionId, ExecutionMode mode) {
        return new ExecutionLifecycle(execution(executionId, mode, true));
    }

    private static ExecutionPolicyControlPlane controlPlane(ConfigurableStore store) {
        return new ExecutionPolicyControlPlane(
                Set.of("legacy-review-v1", "candidate-review-v2"), store, CLOCK);
    }

    private static final class ConfigurableStore implements ExecutionControlStore {
        private Optional<FrozenExecutionPlan> found = Optional.empty();
        private FrozenExecutionPlan created;

        @Override
        public Optional<FrozenExecutionPlan> findPlan(String executionId) {
            return found;
        }

        @Override
        public FrozenExecutionPlan createPlanIfAbsent(FrozenExecutionPlan plan) {
            return created == null ? plan : created;
        }

        @Override
        public void persistArtifact(ExecutionArtifact artifact) {
        }

        @Override
        public List<ExecutionArtifact> findArtifacts(
                String executionId, ArtifactNamespace namespace) {
            return List.of();
        }

        @Override
        public boolean tryClaimPublication(String publicationClaimId) {
            return false;
        }
    }
}
