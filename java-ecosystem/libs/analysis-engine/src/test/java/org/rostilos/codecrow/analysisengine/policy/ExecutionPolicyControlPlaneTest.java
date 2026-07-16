package org.rostilos.codecrow.analysisengine.policy;

import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ExecutionPolicyControlPlaneTest {
    private static final Clock CLOCK = Clock.fixed(
            Instant.parse("2026-07-14T12:00:00Z"), ZoneOffset.UTC);

    @Test
    void freezesDeterministicActiveSelectionFromStableProjectKey() {
        MemoryStore store = new MemoryStore();
        ExecutionPolicyControlPlane controlPlane = new ExecutionPolicyControlPlane(
                Set.of("legacy-review-v1", "candidate-review-v2"), store, CLOCK);
        ExecutionPolicyConfig flags = new ExecutionPolicyConfig(
                "flags-17",
                ExecutionMode.ACTIVE,
                "candidate-review-v2",
                10_000,
                "rollout-salt-v1",
                false,
                false);

        FrozenExecutionPlan first = controlPlane.freeze(
                "execution-0001", StableRolloutKey.forProject(7L, 41L), flags);
        FrozenExecutionPlan second = controlPlane.freeze(
                "execution-0002", StableRolloutKey.forProject(7L, 41L), flags);

        assertThat(first.primary().policyVersion()).isEqualTo("candidate-review-v2");
        assertThat(first.primary().mode()).isEqualTo(ExecutionMode.ACTIVE);
        assertThat(first.primary().selectionReason())
                .isEqualTo(PolicySelectionReason.ACTIVE_ROLLOUT_SELECTED);
        assertThat(first.primary().rolloutBucket()).isEqualTo(second.primary().rolloutBucket());
        assertThat(first.stableRolloutKeyHash()).isEqualTo(second.stableRolloutKeyHash());
        assertThat(first.primary().publicationAllowed()).isTrue();
        assertThat(first.shadow()).isNull();
    }

    @Test
    void candidatePrimaryPreviewMatchesTheFrozenRouteForEveryPolicyMode() {
        StableRolloutKey rolloutKey = StableRolloutKey.forProject(7L, 41L);
        List<ExecutionPolicyConfig> snapshots = List.of(
                config(ExecutionMode.LEGACY, "candidate-review-v2", 10_000, false, false),
                config(ExecutionMode.SHADOW, "candidate-review-v2", 10_000, false, false),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 0, false, false),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 4_321, false, false),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, false),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, true));
        ExecutionPolicyControlPlane controlPlane = controlPlane(new MemoryStore());

        for (int index = 0; index < snapshots.size(); index++) {
            ExecutionPolicyConfig snapshot = snapshots.get(index);
            boolean preview = ExecutionPolicyControlPlane.selectsCandidatePrimary(
                    rolloutKey, snapshot);
            FrozenExecutionPlan frozen = controlPlane.freeze(
                    "execution-preview-" + index, rolloutKey, snapshot);

            assertThat(preview)
                    .as("preview for %s at %s basis points",
                            snapshot.mode(), snapshot.rolloutBasisPoints())
                    .isEqualTo(frozen.primary().candidatePath());
        }
    }

    @Test
    void candidatePrimaryPreviewLeavesStoreFirstAdmissionToFreeze() {
        StableRolloutKey rolloutKey = StableRolloutKey.forProject(7L, 41L);
        ExecutionPolicyConfig stoppedCandidate = config(
                ExecutionMode.ACTIVE,
                "candidate-review-v2",
                10_000,
                true,
                false);
        ExecutionPolicyControlPlane controlPlane = controlPlane(new MemoryStore());

        assertThat(ExecutionPolicyControlPlane.selectsCandidatePrimary(
                rolloutKey, stoppedCandidate)).isTrue();
        assertThatThrownBy(() -> controlPlane.freeze(
                "execution-preview-stopped", rolloutKey, stoppedCandidate))
                .isInstanceOf(NewWorkDisabledException.class);
    }

    @Test
    void rejectsUnknownCandidatePolicyVersionsBeforeCreatingWork() {
        MemoryStore store = new MemoryStore();
        ExecutionPolicyControlPlane controlPlane = controlPlane(store);

        assertThatThrownBy(() -> controlPlane.freeze(
                "execution-unknown",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.ACTIVE, "unknown-review-v9", 10_000, false, false)))
                .isInstanceOf(UnknownExecutionPolicyVersionException.class)
                .hasMessageContaining("unknown-review-v9");
        assertThat(store.plans).isEmpty();
    }

    @Test
    void storeRequiresFrozenPlansWhenLifecycleWorkResumes() {
        MemoryStore store = new MemoryStore();
        FrozenExecutionPlan plan = controlPlane(store).freeze(
                "execution-required",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.LEGACY, "candidate-review-v2", 0, false, false));

        assertThat(store.requirePlan("execution-required")).isSameAs(plan);
        assertThatThrownBy(() -> store.requirePlan("execution-missing"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("execution-missing");
    }

    @Test
    void laterFlagChangesCannotMutateAnExistingExecutionPlan() {
        MemoryStore store = new MemoryStore();
        ExecutionPolicyControlPlane controlPlane = controlPlane(store);
        FrozenExecutionPlan selected = controlPlane.freeze(
                "execution-frozen",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, false));

        FrozenExecutionPlan afterFlagChange = controlPlane.freeze(
                "execution-frozen",
                StableRolloutKey.forProject(999L, 999L),
                config(ExecutionMode.LEGACY, "candidate-review-v2", 0, true, true));

        assertThat(afterFlagChange).isSameAs(selected);
        assertThat(afterFlagChange.configRevision()).isEqualTo("flags-test");
        assertThat(afterFlagChange.primary().mode()).isEqualTo(ExecutionMode.ACTIVE);
    }

    @Test
    void shadowArtifactsAreSeparateAndShadowCannotClaimPublication() {
        MemoryStore store = new MemoryStore();
        ExecutionPolicyControlPlane controlPlane = controlPlane(store);
        FrozenExecutionPlan plan = controlPlane.freeze(
                "execution-shadow",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.SHADOW, "candidate-review-v2", 0, false, false));
        ExecutionArtifactWriter writer = new ExecutionArtifactWriter(store, CLOCK);
        PublicationFence fence = new PublicationFence(store);

        writer.persist(plan.primary(), "primary-result", "{\"result\":\"legacy\"}");
        writer.persist(plan.shadow(), "shadow-result", "{\"result\":\"candidate\"}");
        PublicationReservation reservation = fence.reserve(
                plan.shadow(),
                PublicationKey.forPullRequest("github", 41L, 82L, "a".repeat(40)));

        assertThat(plan.primary().policyVersion()).isEqualTo("legacy-review-v1");
        assertThat(plan.shadow().policyVersion()).isEqualTo("candidate-review-v2");
        assertThat(plan.shadow().publicationAllowed()).isFalse();
        assertThat(store.findArtifacts("execution-shadow", ArtifactNamespace.PRIMARY))
                .extracting(ExecutionArtifact::artifactId)
                .containsExactly("primary-result");
        assertThat(store.findArtifacts("execution-shadow:shadow", ArtifactNamespace.SHADOW))
                .extracting(ExecutionArtifact::artifactId)
                .containsExactly("shadow-result");
        assertThat(reservation).isEqualTo(PublicationReservation.SHADOW_DENIED);
        assertThat(store.publicationClaimAttempts).isZero();
    }

    @Test
    void duplicateWebhookDeliveryCanReservePublicationOnlyOnce() {
        MemoryStore store = new MemoryStore();
        FrozenExecutionPlan plan = controlPlane(store).freeze(
                "execution-delivery",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.LEGACY, "candidate-review-v2", 0, false, false));
        PublicationFence fence = new PublicationFence(store);
        PublicationKey delivery = PublicationKey.forPullRequest(
                "gitlab", 41L, 82L, "b".repeat(40));
        long generation = fence.claimLatestHeadGeneration(
                "admission-delivery", delivery);

        assertThat(fence.registerLatestHead(
                plan.primary(), delivery, generation))
                .isEqualTo(LatestHeadRegistration.ACCEPTED);
        assertThat(fence.reserve(plan.primary(), delivery))
                .isEqualTo(PublicationReservation.RESERVED);
        assertThat(fence.reserve(plan.primary(), delivery))
                .isEqualTo(PublicationReservation.DUPLICATE);
        assertThat(store.publicationClaims).hasSize(1);
    }

    @Test
    void reorderedTwoHeadEventsSupersedeOldWorkAndFenceStalePublication() {
        MemoryStore store = new MemoryStore();
        ExecutionPolicyControlPlane controlPlane = controlPlane(store);
        PolicyExecution first = controlPlane.freeze(
                "execution-head-a",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, false))
                .primary();
        PolicyExecution latest = controlPlane.freeze(
                "execution-head-b",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, false))
                .primary();
        PolicyExecution conflictingLatest = controlPlane.freeze(
                "execution-head-b-rederived",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, false))
                .primary();
        PublicationFence fence = new PublicationFence(store);
        PublicationKey headA = PublicationKey.forPullRequest(
                "github", 41L, 82L, "a".repeat(40));
        PublicationKey headB = PublicationKey.forPullRequest(
                "github", 41L, 82L, "b".repeat(40));

        long firstGeneration = fence.claimLatestHeadGeneration(
                "admission-head-a", headA);
        long latestGeneration = fence.claimLatestHeadGeneration(
                "admission-head-b", headB);

        assertThat(firstGeneration).isLessThan(latestGeneration);
        assertThat(fence.claimLatestHeadGeneration(
                "admission-head-a", headA)).isEqualTo(firstGeneration);
        // The newer acquisition completes first. The older execution has never
        // registered, so a seen-execution set alone cannot protect this race.
        assertThat(fence.registerLatestHead(
                latest, headB, latestGeneration))
                .isEqualTo(LatestHeadRegistration.ACCEPTED);
        assertThat(fence.registerLatestHead(
                latest, headB, latestGeneration))
                .isEqualTo(LatestHeadRegistration.DUPLICATE);
        assertThatThrownBy(() -> fence.registerLatestHead(
                conflictingLatest, headB, latestGeneration))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("already bound");
        assertThat(fence.registerLatestHead(
                first, headA, firstGeneration))
                .isEqualTo(LatestHeadRegistration.SUPERSEDED);
        assertThat(fence.findLatestHeadGeneration(headB))
                .hasValue(latestGeneration);

        assertThat(fence.isLatestHead(first, headA)).isFalse();
        assertThat(fence.reserve(first, headA))
                .isEqualTo(PublicationReservation.STALE_HEAD);
        assertThat(fence.isLatestHead(latest, headB)).isTrue();
        assertThat(fence.reserve(latest, headB))
                .isEqualTo(PublicationReservation.RESERVED);
        assertThat(fence.reserve(latest, headB))
                .isEqualTo(PublicationReservation.DUPLICATE);
    }

    @Test
    void legacyAndCandidatePlansCoexistAndRollbackPreservesArtifacts() {
        MemoryStore store = new MemoryStore();
        ExecutionPolicyControlPlane controlPlane = controlPlane(store);
        FrozenExecutionPlan legacy = controlPlane.freeze(
                "execution-legacy",
                StableRolloutKey.forProject(7L, 40L),
                config(ExecutionMode.LEGACY, "candidate-review-v2", 0, false, false));
        FrozenExecutionPlan candidate = controlPlane.freeze(
                "execution-candidate",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, false));
        new ExecutionArtifactWriter(store, CLOCK).persist(
                candidate.primary(), "candidate-evidence", "{\"kept\":true}");

        FrozenExecutionPlan rolledBack = controlPlane.freeze(
                "execution-after-rollback",
                StableRolloutKey.forProject(7L, 42L),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, true));

        assertThat(legacy.primary().mode()).isEqualTo(ExecutionMode.LEGACY);
        assertThat(candidate.primary().mode()).isEqualTo(ExecutionMode.ACTIVE);
        assertThat(rolledBack.primary().mode()).isEqualTo(ExecutionMode.LEGACY);
        assertThat(rolledBack.primary().selectionReason())
                .isEqualTo(PolicySelectionReason.CANDIDATE_KILL_SWITCH_ROLLBACK);
        assertThat(store.findArtifacts("execution-candidate", ArtifactNamespace.PRIMARY))
                .extracting(ExecutionArtifact::artifactId)
                .containsExactly("candidate-evidence");
    }

    @Test
    void globalStopRejectsNewWorkAndKillSwitchDoesNotRewriteCompletedTruth() {
        MemoryStore store = new MemoryStore();
        ExecutionPolicyControlPlane controlPlane = controlPlane(store);
        ExecutionPolicyConfig active = config(
                ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, false);
        PolicyExecution runningExecution = controlPlane.freeze(
                "execution-running",
                StableRolloutKey.forProject(7L, 41L),
                active).primary();
        PolicyExecution completedExecution = controlPlane.freeze(
                "execution-complete",
                StableRolloutKey.forProject(7L, 42L),
                active).primary();
        ExecutionLifecycle running = new ExecutionLifecycle(runningExecution);
        ExecutionLifecycle completed = new ExecutionLifecycle(completedExecution);
        assertThat(running.start()).isTrue();
        assertThat(completed.start()).isTrue();
        assertThat(completed.complete()).isTrue();
        ExecutionPolicyConfig stopped = config(
                ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, true, true);

        assertThat(running.reconcileKillSwitch(stopped)).isTrue();
        assertThat(running.state()).isEqualTo(ExecutionLifecycleState.CANCELLATION_REQUESTED);
        assertThat(running.markCancelled()).isTrue();
        assertThat(completed.reconcileKillSwitch(stopped)).isFalse();
        assertThat(completed.state()).isEqualTo(ExecutionLifecycleState.COMPLETED);
        assertThatThrownBy(() -> controlPlane.freeze(
                "execution-rejected",
                StableRolloutKey.forProject(7L, 43L),
                stopped))
                .isInstanceOf(NewWorkDisabledException.class);
    }

    @Test
    void freezesCreatedAtAtCrossLanguagePersistencePrecision() {
        Instant nanosecondClock = Instant.parse("2026-07-15T12:00:00.123456789Z");
        ExecutionPolicyControlPlane controlPlane = new ExecutionPolicyControlPlane(
                Set.of("legacy-review-v1", "candidate-review-v2"),
                new MemoryStore(),
                Clock.fixed(nanosecondClock, ZoneOffset.UTC));

        FrozenExecutionPlan plan = controlPlane.freeze(
                "execution-microseconds",
                StableRolloutKey.forProject(7L, 41L),
                config(ExecutionMode.ACTIVE, "candidate-review-v2", 10_000, false, false));

        assertThat(plan.createdAt())
                .isEqualTo(Instant.parse("2026-07-15T12:00:00.123456Z"));
        assertThat(plan.primary().createdAt()).isEqualTo(plan.createdAt());
    }

    private ExecutionPolicyControlPlane controlPlane(MemoryStore store) {
        return new ExecutionPolicyControlPlane(
                Set.of("legacy-review-v1", "candidate-review-v2"), store, CLOCK);
    }

    private ExecutionPolicyConfig config(
            ExecutionMode mode,
            String candidateVersion,
            int rolloutBasisPoints,
            boolean stopNewWork,
            boolean candidateKillSwitch) {
        return new ExecutionPolicyConfig(
                "flags-test",
                mode,
                candidateVersion,
                rolloutBasisPoints,
                "rollout-salt-v1",
                stopNewWork,
                candidateKillSwitch);
    }

    private static final class MemoryStore implements ExecutionControlStore {
        private final Map<String, FrozenExecutionPlan> plans = new HashMap<>();
        private final List<ExecutionArtifact> artifacts = new ArrayList<>();
        private final Set<String> publicationClaims = new java.util.HashSet<>();
        private final Map<String, String> latestHeads = new HashMap<>();
        private final Map<String, Long> latestGenerations = new HashMap<>();
        private final Map<String, Long> generationCounters = new HashMap<>();
        private final Map<String, Map<String, Long>> admissionGenerations = new HashMap<>();
        private int publicationClaimAttempts;

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
                    .filter(artifact -> artifact.executionId().equals(executionId))
                    .filter(artifact -> artifact.namespace() == namespace)
                    .toList();
        }

        @Override
        public boolean tryClaimPublication(String publicationClaimId) {
            publicationClaimAttempts++;
            return publicationClaims.add(publicationClaimId);
        }

        @Override
        public long claimLatestHeadGeneration(
                String publicationScopeId,
                String admissionId) {
            Map<String, Long> claimed = admissionGenerations.computeIfAbsent(
                    publicationScopeId, ignored -> new HashMap<>());
            return claimed.computeIfAbsent(admissionId, ignored -> {
                long next = generationCounters.getOrDefault(
                        publicationScopeId, 0L) + 1L;
                generationCounters.put(publicationScopeId, next);
                return next;
            });
        }

        @Override
        public OptionalLong findLatestHeadGeneration(String publicationScopeId) {
            Long generation = latestGenerations.get(publicationScopeId);
            return generation == null
                    ? OptionalLong.empty()
                    : OptionalLong.of(generation);
        }

        @Override
        public LatestHeadRegistration registerLatestHead(
                String publicationScopeId,
                String executionId,
                String headRevision,
                long generation) {
            boolean claimed = admissionGenerations
                    .getOrDefault(publicationScopeId, Map.of())
                    .containsValue(generation);
            if (!claimed) {
                throw new IllegalStateException(
                        "latest-head generation was not claimed");
            }
            String candidate = executionId + '\n' + headRevision;
            Long currentGeneration = latestGenerations.get(publicationScopeId);
            if (currentGeneration != null) {
                if (generation < currentGeneration) {
                    return LatestHeadRegistration.SUPERSEDED;
                }
                if (generation == currentGeneration) {
                    if (candidate.equals(latestHeads.get(publicationScopeId))) {
                        return LatestHeadRegistration.DUPLICATE;
                    }
                    throw new IllegalStateException(
                            "latest-head generation is already bound");
                }
            }
            latestGenerations.put(publicationScopeId, generation);
            latestHeads.put(publicationScopeId, candidate);
            return LatestHeadRegistration.ACCEPTED;
        }

        @Override
        public boolean isLatestHead(
                String publicationScopeId,
                String executionId,
                String headRevision) {
            return (executionId + '\n' + headRevision).equals(
                    latestHeads.get(publicationScopeId));
        }

        @Override
        public PublicationReservation tryClaimLatestPublication(
                String publicationScopeId,
                String executionId,
                String headRevision,
                String publicationClaimId) {
            if (!isLatestHead(publicationScopeId, executionId, headRevision)) {
                return PublicationReservation.STALE_HEAD;
            }
            return tryClaimPublication(publicationClaimId)
                    ? PublicationReservation.RESERVED
                    : PublicationReservation.DUPLICATE;
        }
    }
}
