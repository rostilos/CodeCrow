package org.rostilos.codecrow.analysisengine.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.Test;

class ReviewDeliveryServiceTest {
    private static final String INTENT_ID = "delivery:vs14:summary";
    private static final String EXECUTION_ID = "execution:vs14";
    private static final String MANIFEST_DIGEST = "1".repeat(64);
    private static final String HEAD_SHA = "2".repeat(40);
    private static final long HEAD_GENERATION = 7L;
    private static final long TENANT_ID = 9L;
    private static final long PROJECT_ID = 13L;
    private static final long PR_ID = 42L;
    private static final String REPOSITORY_ID = "github:acme/review-api";
    private static final String REPORT_ARTIFACT_ID =
            "review-output:" + "3".repeat(64);
    private static final String REPORT_DIGEST = "4".repeat(64);
    private static final String ANALYSIS_TRUTH_DIGEST = "5".repeat(64);
    private static final String IDEMPOTENCY_KEY = "6".repeat(64);
    private static final Instant FIRST_ATTEMPT =
            Instant.parse("2026-07-16T10:00:00Z");

    @Test
    void transientFailureRetriesAfterFreshServiceWithSameIdempotencyKey() {
        InMemoryOutbox outbox = new InMemoryOutbox();
        List<String> providerKeys = new ArrayList<>();
        ReviewDeliveryGateway transientGateway = claim -> {
            providerKeys.add(claim.intent().idempotencyKey());
            return outcome(
                    claim,
                    ReviewDeliveryState.RETRYABLE_FAILED,
                    "provider_unavailable",
                    null);
        };
        ReviewDeliveryService firstProcess = new ReviewDeliveryService(
                outbox, transientGateway, ignored -> true);

        assertThat(firstProcess.registerCurrentHead(head()))
                .isEqualTo(head());
        assertThat(firstProcess.enqueue(intent(ANALYSIS_TRUTH_DIGEST)))
                .contains(intent(ANALYSIS_TRUTH_DIGEST));
        ReviewDeliveryOutcome failed = firstProcess.attempt(
                INTENT_ID, FIRST_ATTEMPT);

        assertThat(failed.state())
                .isEqualTo(ReviewDeliveryState.RETRYABLE_FAILED);
        assertThat(failed.attemptCount()).isOne();
        assertThat(failed.idempotencyKey()).isEqualTo(IDEMPOTENCY_KEY);
        assertThat(outbox.findOutcome(INTENT_ID)).contains(failed);

        ReviewDeliveryGateway recoveredGateway = claim -> {
            providerKeys.add(claim.intent().idempotencyKey());
            return outcome(
                    claim,
                    ReviewDeliveryState.DELIVERED,
                    null,
                    "provider-receipt-vs14");
        };
        ReviewDeliveryService restartedProcess = new ReviewDeliveryService(
                outbox, recoveredGateway, ignored -> true);
        ReviewDeliveryOutcome delivered = restartedProcess.attempt(
                INTENT_ID, FIRST_ATTEMPT.plusSeconds(60));

        assertThat(delivered.state()).isEqualTo(ReviewDeliveryState.DELIVERED);
        assertThat(delivered.attemptCount()).isEqualTo(2);
        assertThat(delivered.idempotencyKey()).isEqualTo(IDEMPOTENCY_KEY);
        assertThat(delivered.providerReceiptId())
                .isEqualTo("provider-receipt-vs14");
        assertThat(providerKeys)
                .containsExactly(IDEMPOTENCY_KEY, IDEMPOTENCY_KEY);
        assertThat(outbox.findIntent(INTENT_ID))
                .contains(intent(ANALYSIS_TRUTH_DIGEST));
    }

    @Test
    void deliveredIntentIsReturnedWithoutCallingProviderAgain() {
        InMemoryOutbox outbox = new InMemoryOutbox();
        AtomicInteger providerCalls = new AtomicInteger();
        ReviewDeliveryService firstProcess = new ReviewDeliveryService(
                outbox,
                claim -> {
                    providerCalls.incrementAndGet();
                    return outcome(
                            claim,
                            ReviewDeliveryState.DELIVERED,
                            null,
                            "provider-receipt-once");
                },
                ignored -> true);
        firstProcess.registerCurrentHead(head());
        firstProcess.enqueue(intent(ANALYSIS_TRUTH_DIGEST));
        ReviewDeliveryOutcome first = firstProcess.attempt(
                INTENT_ID, FIRST_ATTEMPT);

        ReviewDeliveryService restartedProcess = new ReviewDeliveryService(
                outbox,
                claim -> {
                    throw new AssertionError(
                            "a delivered intent must not reach the provider again");
                },
                ignored -> true);
        ReviewDeliveryOutcome replay = restartedProcess.attempt(
                INTENT_ID, FIRST_ATTEMPT.plusSeconds(60));

        assertThat(replay).isEqualTo(first);
        assertThat(replay.state()).isEqualTo(ReviewDeliveryState.DELIVERED);
        assertThat(replay.attemptCount()).isOne();
        assertThat(providerCalls).hasValue(1);
        assertThat(outbox.claimCount()).isOne();
    }

    @Test
    void divergentTruthFailsClosedBeforeProviderCall() {
        InMemoryOutbox divergentOutbox = new InMemoryOutbox();
        AtomicInteger providerCalls = new AtomicInteger();
        ReviewDeliveryGateway countingGateway = claim -> {
            providerCalls.incrementAndGet();
            return outcome(
                    claim,
                    ReviewDeliveryState.DELIVERED,
                    null,
                    "must-not-be-created");
        };
        ReviewDeliveryService service = new ReviewDeliveryService(
                divergentOutbox, countingGateway, ignored -> true);
        service.registerCurrentHead(head());
        service.enqueue(intent(ANALYSIS_TRUTH_DIGEST));

        assertThatThrownBy(() -> service.enqueue(intent("7".repeat(64))))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("analysis truth");
        assertThat(providerCalls).hasValue(0);
        assertThat(divergentOutbox.claimCount()).isZero();
    }

    @Test
    void staleHeadIsRejectedBeforeOutboxMutation() {
        AtomicInteger providerCalls = new AtomicInteger();
        InMemoryOutbox staleOutbox = new InMemoryOutbox();
        ReviewDeliveryService staleService = new ReviewDeliveryService(
                staleOutbox,
                claim -> {
                    providerCalls.incrementAndGet();
                    return outcome(
                            claim,
                            ReviewDeliveryState.DELIVERED,
                            null,
                            "must-not-be-created");
                },
                ignored -> false);

        assertThat(staleService.enqueue(intent(ANALYSIS_TRUTH_DIGEST)))
                .isEmpty();

        assertThat(providerCalls).hasValue(0);
        assertThat(staleOutbox.createCount()).isZero();
        assertThat(staleOutbox.findIntent(INTENT_ID)).isEmpty();
        assertThat(staleOutbox.claimCount()).isZero();
    }

    @Test
    void transactionalHeadRaceRejectsBeforeOutboxMutation() {
        InMemoryOutbox outbox = new InMemoryOutbox();
        outbox.registerCurrentHead(head());
        outbox.rejectNextAdmissionAsSuperseded();
        ReviewDeliveryService service = new ReviewDeliveryService(
                outbox,
                claim -> {
                    throw new AssertionError("stale admission reached provider");
                },
                ignored -> true);

        assertThat(service.enqueue(intent(ANALYSIS_TRUTH_DIGEST))).isEmpty();
        assertThat(outbox.createCount()).isOne();
        assertThat(outbox.findIntent(INTENT_ID)).isEmpty();
        assertThat(outbox.claimCount()).isZero();
    }

    @Test
    void effectStartIsDurableBeforeGateway() {
        InMemoryOutbox outbox = new InMemoryOutbox();
        outbox.failNextEffectStart();
        AtomicInteger providerCalls = new AtomicInteger();
        ReviewDeliveryService service = new ReviewDeliveryService(
                outbox,
                claim -> {
                    providerCalls.incrementAndGet();
                    return outcome(
                            claim,
                            ReviewDeliveryState.DELIVERED,
                            null,
                            "must-not-exist");
                },
                ignored -> true);
        service.registerCurrentHead(head());
        service.enqueue(intent(ANALYSIS_TRUTH_DIGEST));

        assertThatThrownBy(() -> service.attempt(INTENT_ID, FIRST_ATTEMPT))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("effect start");
        assertThat(providerCalls).hasValue(0);
        assertThat(outbox.findOutcome(INTENT_ID))
                .hasValueSatisfying(value -> assertThat(value.state())
                        .isEqualTo(ReviewDeliveryState.IN_FLIGHT));
    }

    @Test
    void lostLocalAcknowledgementRemainsAmbiguousAndNeverRepeatsProviderEffect() {
        InMemoryOutbox outbox = new InMemoryOutbox();
        outbox.failNextOutcomeAcknowledgement();
        AtomicInteger providerEffects = new AtomicInteger();
        ReviewDeliveryService firstProcess = new ReviewDeliveryService(
                outbox,
                claim -> {
                    providerEffects.incrementAndGet();
                    return outcome(
                            claim,
                            ReviewDeliveryState.DELIVERED,
                            null,
                            "provider-receipt-lost-locally");
                },
                ignored -> true);
        firstProcess.registerCurrentHead(head());
        firstProcess.enqueue(intent(ANALYSIS_TRUTH_DIGEST));

        assertThatThrownBy(() -> firstProcess.attempt(INTENT_ID, FIRST_ATTEMPT))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("acknowledgement");
        assertThat(providerEffects).hasValue(1);
        assertThat(outbox.findOutcome(INTENT_ID))
                .hasValueSatisfying(value -> {
                    assertThat(value.state())
                            .isEqualTo(ReviewDeliveryState.AMBIGUOUS);
                    assertThat(value.reasonCode())
                            .isEqualTo("provider_ack_unknown");
                });

        ReviewDeliveryService restarted = new ReviewDeliveryService(
                outbox,
                claim -> {
                    throw new AssertionError(
                            "ambiguous effect must never be replayed automatically");
                },
                ignored -> true);
        ReviewDeliveryOutcome replay = restarted.attempt(
                INTENT_ID, FIRST_ATTEMPT.plusSeconds(120));

        assertThat(replay.state()).isEqualTo(ReviewDeliveryState.AMBIGUOUS);
        assertThat(providerEffects).hasValue(1);
        assertThat(outbox.claimCount()).isOne();
    }

    @Test
    void permanentAndAmbiguousProviderReceiptsAreTerminalAcrossRestart() {
        for (ReviewDeliveryState state : List.of(
                ReviewDeliveryState.PERMANENT_FAILED,
                ReviewDeliveryState.AMBIGUOUS)) {
            InMemoryOutbox outbox = new InMemoryOutbox();
            AtomicInteger providerCalls = new AtomicInteger();
            ReviewDeliveryService first = new ReviewDeliveryService(
                    outbox,
                    claim -> {
                        providerCalls.incrementAndGet();
                        return outcome(
                                claim,
                                state,
                                state == ReviewDeliveryState.AMBIGUOUS
                                        ? "provider_ack_unknown"
                                        : "pull_request_deleted",
                                null);
                    },
                    ignored -> true);
            first.registerCurrentHead(head());
            first.enqueue(intent(ANALYSIS_TRUTH_DIGEST));
            ReviewDeliveryOutcome terminal = first.attempt(
                    INTENT_ID, FIRST_ATTEMPT);

            ReviewDeliveryService restarted = new ReviewDeliveryService(
                    outbox,
                    claim -> {
                        throw new AssertionError("terminal receipt was replayed");
                    },
                    ignored -> true);
            assertThat(restarted.attempt(
                    INTENT_ID, FIRST_ATTEMPT.plusSeconds(60)))
                    .isEqualTo(terminal);
            assertThat(providerCalls).hasValue(1);
            assertThat(outbox.claimCount()).isOne();
        }
    }

    private static ReviewDeliveryHead head() {
        return new ReviewDeliveryHead(
                "github",
                TENANT_ID,
                PROJECT_ID,
                REPOSITORY_ID,
                PR_ID,
                EXECUTION_ID,
                HEAD_SHA,
                HEAD_GENERATION);
    }

    private static ReviewDeliveryIntent intent(String analysisTruthDigest) {
        return new ReviewDeliveryIntent(
                INTENT_ID,
                EXECUTION_ID,
                MANIFEST_DIGEST,
                HEAD_SHA,
                HEAD_GENERATION,
                REPORT_ARTIFACT_ID,
                REPORT_DIGEST,
                analysisTruthDigest,
                "github",
                PROJECT_ID,
                PR_ID,
                "SUMMARY",
                IDEMPOTENCY_KEY);
    }

    private static ReviewDeliveryOutcome outcome(
            ReviewDeliveryClaim claim,
            ReviewDeliveryState state,
            String reasonCode,
            String providerReceiptId) {
        return new ReviewDeliveryOutcome(
                state,
                claim.intent().intentId(),
                claim.intent().idempotencyKey(),
                claim.attemptNumber(),
                reasonCode,
                providerReceiptId);
    }

    private static final class InMemoryOutbox
            implements ReviewDeliveryOutboxPort {
        private ReviewDeliveryHead currentHead;
        private ReviewDeliveryIntent storedIntent;
        private ReviewDeliveryOutcome storedOutcome;
        private ReviewDeliveryClaim activeClaim;
        private int attempts;
        private int claims;
        private int creates;
        private final AtomicBoolean rejectNextAdmission = new AtomicBoolean();
        private final AtomicBoolean failNextEffectStart = new AtomicBoolean();
        private final AtomicBoolean failNextAcknowledgement = new AtomicBoolean();

        @Override
        public ReviewDeliveryHead registerCurrentHead(
                ReviewDeliveryHead proposed) {
            if (currentHead == null
                    || proposed.generation() > currentHead.generation()) {
                currentHead = proposed;
                return proposed;
            }
            if (proposed.generation() == currentHead.generation()
                    && !proposed.equals(currentHead)) {
                throw new IllegalStateException(
                        "delivery head generation conflicts with durable identity");
            }
            return currentHead;
        }

        @Override
        public Optional<ReviewDeliveryIntent> createOrLoadIfCurrent(
                ReviewDeliveryIntent proposed) {
            creates++;
            if (rejectNextAdmission.getAndSet(false)
                    || currentHead == null
                    || !currentHead.executionId().equals(proposed.executionId())
                    || !currentHead.headRevision().equals(
                            proposed.snapshotRevision())
                    || currentHead.generation() != proposed.headGeneration()) {
                return Optional.empty();
            }
            if (storedIntent == null) {
                storedIntent = proposed;
                return Optional.of(proposed);
            }
            if (!storedIntent.equals(proposed)) {
                throw new IllegalStateException(
                        "delivery intent conflicts with durable analysis truth");
            }
            return Optional.of(storedIntent);
        }

        @Override
        public Optional<ReviewDeliveryIntent> findIntent(String intentId) {
            return storedIntent != null && storedIntent.intentId().equals(intentId)
                    ? Optional.of(storedIntent)
                    : Optional.empty();
        }

        @Override
        public Optional<ReviewDeliveryClaim> tryClaim(
                String intentId, Instant now) {
            if (storedIntent == null
                    || !storedIntent.intentId().equals(intentId)
                    || storedOutcome != null
                            && storedOutcome.state()
                                    != ReviewDeliveryState.RETRYABLE_FAILED) {
                return Optional.empty();
            }
            attempts++;
            claims++;
            activeClaim = new ReviewDeliveryClaim(
                    storedIntent,
                    attempts,
                    "lease-vs14-" + attempts);
            storedOutcome = outcome(
                    activeClaim,
                    ReviewDeliveryState.IN_FLIGHT,
                    null,
                    null);
            return Optional.of(activeClaim);
        }

        @Override
        public ReviewDeliveryOutcome markEffectStarted(
                ReviewDeliveryClaim claim,
                Instant now) {
            assertThat(claim).isEqualTo(activeClaim);
            if (failNextEffectStart.getAndSet(false)) {
                throw new IllegalStateException(
                        "delivery effect start could not be persisted");
            }
            storedOutcome = outcome(
                    claim,
                    ReviewDeliveryState.AMBIGUOUS,
                    "provider_ack_unknown",
                    null);
            return storedOutcome;
        }

        @Override
        public ReviewDeliveryOutcome recordOutcome(
                ReviewDeliveryClaim claim,
                ReviewDeliveryOutcome outcome,
                Instant now) {
            assertThat(claim.intent()).isEqualTo(storedIntent);
            assertThat(outcome.intentId()).isEqualTo(storedIntent.intentId());
            assertThat(outcome.idempotencyKey())
                    .isEqualTo(storedIntent.idempotencyKey());
            assertThat(outcome.attemptCount()).isEqualTo(claim.attemptNumber());
            if (outcome.state() != ReviewDeliveryState.STALE) {
                assertThat(storedOutcome.state())
                        .isEqualTo(ReviewDeliveryState.AMBIGUOUS);
            }
            if (failNextAcknowledgement.getAndSet(false)) {
                throw new IllegalStateException(
                        "delivery outcome acknowledgement was lost");
            }
            storedOutcome = outcome;
            activeClaim = null;
            return outcome;
        }

        @Override
        public Optional<ReviewDeliveryOutcome> findOutcome(String intentId) {
            return storedIntent != null
                            && storedIntent.intentId().equals(intentId)
                            && storedOutcome != null
                    ? Optional.of(storedOutcome)
                    : Optional.empty();
        }

        private int claimCount() {
            return claims;
        }

        private int createCount() {
            return creates;
        }

        private void rejectNextAdmissionAsSuperseded() {
            rejectNextAdmission.set(true);
        }

        private void failNextEffectStart() {
            failNextEffectStart.set(true);
        }

        private void failNextOutcomeAcknowledgement() {
            failNextAcknowledgement.set(true);
        }
    }
}
