package org.rostilos.codecrow.analysisengine.policy;

import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.mock.env.MockEnvironment;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ExecutionPolicyRuntimeTest {
    private static final Clock CLOCK = Clock.fixed(
            Instant.parse("2026-07-14T12:00:00Z"), ZoneOffset.UTC);

    @Test
    void springSelectsTheProductionConstructorWhenTheClockConstructorAlsoExists() {
        try (AnnotationConfigApplicationContext context =
                new AnnotationConfigApplicationContext()) {
            context.registerBean(ExecutionControlStore.class, MemoryStore::new);
            context.register(ExecutionPolicyRuntime.class);
            context.refresh();

            assertThat(context.getBean(ExecutionPolicyRuntime.class).currentConfig().mode())
                    .isEqualTo(ExecutionMode.ACTIVE);
        }
    }

    @Test
    void defaultsToTheManifestBoundPathForEveryNewReview() {
        ExecutionPolicyRuntime runtime = new ExecutionPolicyRuntime(
                new MockEnvironment(), new MemoryStore(), CLOCK);

        ExecutionPolicyConfig config = runtime.currentConfig();

        assertThat(config.mode()).isEqualTo(ExecutionMode.ACTIVE);
        assertThat(config.rolloutBasisPoints()).isEqualTo(10_000);
        assertThat(config.stopNewWork()).isFalse();
        assertThat(config.candidateKillSwitch()).isFalse();
        assertThat(runtime.knownPolicyVersions())
                .contains("legacy-review-v1", "candidate-review-v1");
    }

    @Test
    void readsOneValidatedSnapshotAndDerivesAuditableRevision() {
        MockEnvironment environment = new MockEnvironment()
                .withProperty(ExecutionPolicyRuntime.MODE_PROPERTY, "shadow")
                .withProperty(ExecutionPolicyRuntime.CANDIDATE_VERSION_PROPERTY, "candidate-review-v2")
                .withProperty(ExecutionPolicyRuntime.KNOWN_VERSIONS_PROPERTY,
                        "legacy-review-v1,candidate-review-v2")
                .withProperty(ExecutionPolicyRuntime.ROLLOUT_BASIS_POINTS_PROPERTY, "2500")
                .withProperty(ExecutionPolicyRuntime.CANDIDATE_KILL_SWITCH_PROPERTY, "false");
        ExecutionPolicyRuntime runtime = new ExecutionPolicyRuntime(
                environment, new MemoryStore(), CLOCK);

        FrozenExecutionPlan plan = runtime.freeze(
                "runtime-execution", StableRolloutKey.forProject(4L, 8L));

        assertThat(plan.configRevision()).startsWith("cfg-");
        assertThat(plan.primary().mode()).isEqualTo(ExecutionMode.LEGACY);
        assertThat(plan.shadow()).isNotNull();
        assertThat(plan.shadow().policyVersion()).isEqualTo("candidate-review-v2");
    }

    @Test
    void freezeUsesTheAlreadyCapturedSemanticIdentitySnapshot() {
        MockEnvironment environment = new MockEnvironment()
                .withProperty(ExecutionPolicyRuntime.MODE_PROPERTY, "legacy")
                .withProperty(ExecutionPolicyRuntime.CANDIDATE_VERSION_PROPERTY,
                        "candidate-review-v2")
                .withProperty(ExecutionPolicyRuntime.KNOWN_VERSIONS_PROPERTY,
                        "legacy-review-v1,candidate-review-v2");
        ExecutionPolicyRuntime runtime = new ExecutionPolicyRuntime(
                environment, new MemoryStore(), CLOCK);
        ExecutionPolicyConfig captured = new ExecutionPolicyConfig(
                "captured-policy-revision",
                ExecutionMode.ACTIVE,
                "candidate-review-v2",
                10_000,
                "captured-salt",
                false,
                false);

        FrozenExecutionPlan plan = runtime.freeze(
                "captured-input-identity",
                StableRolloutKey.forProject(4L, 8L),
                captured);

        assertThat(plan.configRevision()).isEqualTo("captured-policy-revision");
        assertThat(plan.primary().mode()).isEqualTo(ExecutionMode.ACTIVE);
        assertThat(plan.primary().policyVersion()).isEqualTo("candidate-review-v2");
    }

    @Test
    void rejectsMalformedFlagValuesInsteadOfSilentlyFallingBack() {
        MockEnvironment environment = new MockEnvironment()
                .withProperty(ExecutionPolicyRuntime.STOP_NEW_WORK_PROPERTY, "sometimes");
        ExecutionPolicyRuntime runtime = new ExecutionPolicyRuntime(
                environment, new MemoryStore(), CLOCK);

        assertThatThrownBy(runtime::currentConfig)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("must be true or false");
    }

    @Test
    void publicRuntimeConstructorExposesFenceAndArtifactWriter() {
        ExecutionPolicyRuntime runtime = new ExecutionPolicyRuntime(
                new MockEnvironment(), new MemoryStore());

        assertThat(runtime.currentConfig().mode()).isEqualTo(ExecutionMode.ACTIVE);
        assertThat(runtime.publicationFence()).isNotNull();
        assertThat(runtime.artifactWriter()).isNotNull();
    }

    @Test
    void rejectsUnknownModeAndNonIntegerRollout() {
        ExecutionPolicyRuntime badMode = new ExecutionPolicyRuntime(
                new MockEnvironment().withProperty(ExecutionPolicyRuntime.MODE_PROPERTY, "future"),
                new MemoryStore(),
                CLOCK);
        ExecutionPolicyRuntime badRollout = new ExecutionPolicyRuntime(
                new MockEnvironment().withProperty(
                        ExecutionPolicyRuntime.ROLLOUT_BASIS_POINTS_PROPERTY, "one"),
                new MemoryStore(),
                CLOCK);

        assertThatThrownBy(badMode::currentConfig)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Unknown execution policy mode: FUTURE");
        assertThatThrownBy(badRollout::currentConfig)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("must be an integer");
    }

    @Test
    void trimsExplicitRevisionAndNormalizesConfiguredVersionSet() {
        MockEnvironment environment = new MockEnvironment()
                .withProperty(ExecutionPolicyRuntime.MODE_PROPERTY, "  active  ")
                .withProperty(ExecutionPolicyRuntime.CONFIG_REVISION_PROPERTY, "  release-42  ")
                .withProperty(ExecutionPolicyRuntime.KNOWN_VERSIONS_PROPERTY,
                        " legacy-review-v1, ,candidate-review-v2,candidate-review-v2 ")
                .withProperty(ExecutionPolicyRuntime.STOP_NEW_WORK_PROPERTY, "TRUE")
                .withProperty(ExecutionPolicyRuntime.CANDIDATE_KILL_SWITCH_PROPERTY, "false")
                .withProperty(ExecutionPolicyRuntime.ROLLOUT_SALT_PROPERTY, "   ");
        ExecutionPolicyRuntime runtime = new ExecutionPolicyRuntime(
                environment, new MemoryStore(), CLOCK);

        ExecutionPolicyConfig config = runtime.currentConfig();

        assertThat(config.configRevision()).isEqualTo("release-42");
        assertThat(config.mode()).isEqualTo(ExecutionMode.ACTIVE);
        assertThat(config.stopNewWork()).isTrue();
        assertThat(config.candidateKillSwitch()).isFalse();
        assertThat(config.rolloutSalt()).isEqualTo("codecrow-project-rollout-v1");
        assertThat(runtime.knownPolicyVersions())
                .containsExactlyInAnyOrder("legacy-review-v1", "candidate-review-v2");
    }

    @Test
    void blankRevisionAndBooleanPropertiesUseSafeDefaults() {
        MockEnvironment environment = new MockEnvironment()
                .withProperty(ExecutionPolicyRuntime.CONFIG_REVISION_PROPERTY, "   ")
                .withProperty(ExecutionPolicyRuntime.STOP_NEW_WORK_PROPERTY, "   ")
                .withProperty(ExecutionPolicyRuntime.CANDIDATE_KILL_SWITCH_PROPERTY, "true");
        ExecutionPolicyRuntime runtime = new ExecutionPolicyRuntime(
                environment, new MemoryStore(), CLOCK);

        ExecutionPolicyConfig config = runtime.currentConfig();

        assertThat(config.configRevision()).startsWith("cfg-");
        assertThat(config.stopNewWork()).isFalse();
        assertThat(config.candidateKillSwitch()).isTrue();
    }

    @Test
    void durableCanaryRollbackPreservesFrozenWorkAndRoutesNewWorkToLegacy() {
        MockEnvironment environment = new MockEnvironment()
                .withProperty(ExecutionPolicyRuntime.MODE_PROPERTY, "active")
                .withProperty(
                        ExecutionPolicyRuntime.ROLLOUT_BASIS_POINTS_PROPERTY,
                        "10000")
                .withProperty(
                        ExecutionPolicyRuntime.CONFIG_REVISION_PROPERTY,
                        "release-canary-1");
        MemoryStore store = new MemoryStore();
        ExecutionPolicyRuntime runtime = new ExecutionPolicyRuntime(
                environment, store, CLOCK);

        FrozenExecutionPlan alreadyFrozen = runtime.freeze(
                "candidate-before-breach",
                StableRolloutKey.forProject(4L, 8L));
        store.activateCandidateKillSwitch(
                "{\"schemaVersion\":\"canary-rollback-v1\","
                        + "\"decisionId\":\"" + "d".repeat(64) + "\"}");

        assertThat(runtime.freeze(
                "candidate-before-breach",
                StableRolloutKey.forProject(4L, 8L)))
                .isEqualTo(alreadyFrozen);
        assertThat(alreadyFrozen.primary().mode())
                .isEqualTo(ExecutionMode.ACTIVE);

        ExecutionPolicyConfig rolledBackConfig = runtime.currentConfig();
        FrozenExecutionPlan afterBreach = runtime.freeze(
                "legacy-after-breach",
                StableRolloutKey.forProject(4L, 8L),
                rolledBackConfig);

        assertThat(rolledBackConfig.candidateKillSwitch()).isTrue();
        assertThat(rolledBackConfig.configRevision())
                .startsWith("rollback-")
                .isNotEqualTo("release-canary-1");
        assertThat(afterBreach.primary().mode()).isEqualTo(ExecutionMode.LEGACY);
        assertThat(afterBreach.primary().policyVersion())
                .isEqualTo(ExecutionPolicyControlPlane.LEGACY_POLICY_VERSION);
        assertThat(afterBreach.primary().selectionReason())
                .isEqualTo(
                        PolicySelectionReason.CANDIDATE_KILL_SWITCH_ROLLBACK);
    }

    private static final class MemoryStore implements ExecutionControlStore {
        private final Map<String, FrozenExecutionPlan> plans = new HashMap<>();
        private final List<ExecutionArtifact> artifacts = new ArrayList<>();
        private final Set<String> claims = new java.util.HashSet<>();
        private String rollbackReceipt;

        @Override
        public Optional<FrozenExecutionPlan> findPlan(String executionId) {
            return Optional.ofNullable(plans.get(executionId));
        }

        @Override
        public FrozenExecutionPlan createPlanIfAbsent(FrozenExecutionPlan plan) {
            plans.putIfAbsent(plan.executionId(), plan);
            return plans.get(plan.executionId());
        }

        @Override
        public void persistArtifact(ExecutionArtifact artifact) {
            artifacts.add(artifact);
        }

        @Override
        public List<ExecutionArtifact> findArtifacts(
                String executionId, ArtifactNamespace namespace) {
            return artifacts.stream()
                    .filter(value -> value.executionId().equals(executionId))
                    .filter(value -> value.namespace() == namespace)
                    .toList();
        }

        @Override
        public boolean tryClaimPublication(String publicationClaimId) {
            return claims.add(publicationClaimId);
        }

        @Override
        public synchronized String activateCandidateKillSwitch(
                String receiptJson) {
            if (rollbackReceipt == null) {
                rollbackReceipt = receiptJson;
            }
            return rollbackReceipt;
        }

        @Override
        public Optional<String> findCandidateKillSwitchReceipt() {
            return Optional.ofNullable(rollbackReceipt);
        }
    }
}
