package org.rostilos.codecrow.analysisengine.delivery;

import java.time.Instant;
import java.util.Objects;
import java.util.Optional;
import java.util.function.Predicate;

/** Coordinates durable, restart-safe delivery without recomputing analysis. */
public final class ReviewDeliveryService {
    private final ReviewDeliveryOutboxPort outbox;
    private final ReviewDeliveryGateway gateway;
    private final Predicate<ReviewDeliveryIntent> eligibility;

    public ReviewDeliveryService(
            ReviewDeliveryOutboxPort outbox,
            ReviewDeliveryGateway gateway,
            Predicate<ReviewDeliveryIntent> eligibility) {
        this.outbox = Objects.requireNonNull(outbox, "outbox");
        this.gateway = Objects.requireNonNull(gateway, "gateway");
        this.eligibility = Objects.requireNonNull(eligibility, "eligibility");
    }

    /** Atomically advances or reloads the durable latest-head identity. */
    public ReviewDeliveryHead registerCurrentHead(ReviewDeliveryHead head) {
        Objects.requireNonNull(head, "head");
        ReviewDeliveryHead stored = Objects.requireNonNull(
                outbox.registerCurrentHead(head),
                "registerCurrentHead returned null");
        if (!head.samePullRequestCoordinate(stored)
                || stored.generation() < head.generation()
                || (stored.generation() == head.generation()
                        && !stored.equals(head))) {
            throw new IllegalStateException(
                    "durable delivery head conflicts with proposed identity");
        }
        return stored;
    }

    /** Creates the immutable intent or proves that an exact intent already exists. */
    public Optional<ReviewDeliveryIntent> enqueue(ReviewDeliveryIntent intent) {
        Objects.requireNonNull(intent, "intent");
        if (!eligibility.test(intent)) {
            return Optional.empty();
        }
        Optional<ReviewDeliveryIntent> admitted = Objects.requireNonNull(
                outbox.createOrLoadIfCurrent(intent),
                "createOrLoadIfCurrent returned null");
        if (admitted.isEmpty()) {
            return Optional.empty();
        }
        ReviewDeliveryIntent stored = admitted.orElseThrow();
        if (!intent.equals(stored)) {
            throw new IllegalStateException(
                    "delivery intent conflicts with durable analysis truth");
        }
        return Optional.of(stored);
    }

    /** Claims and executes one due attempt, or returns its existing terminal truth. */
    public ReviewDeliveryOutcome attempt(String intentId, Instant now) {
        requireIntentId(intentId);
        Objects.requireNonNull(now, "now");

        Optional<ReviewDeliveryOutcome> existing = outbox.findOutcome(intentId);
        if (existing.filter(ReviewDeliveryService::terminalNoOp).isPresent()) {
            return existing.orElseThrow();
        }

        ReviewDeliveryIntent intent = outbox.findIntent(intentId)
                .orElseThrow(() -> new IllegalStateException(
                        "delivery intent does not exist: " + intentId));
        Optional<ReviewDeliveryClaim> claimed = outbox.tryClaim(intentId, now);
        if (claimed.isEmpty()) {
            return outbox.findOutcome(intentId)
                    .filter(ReviewDeliveryService::terminalNoOp)
                    .orElseThrow(() -> new IllegalStateException(
                            "delivery intent is not claimable: " + intentId));
        }
        ReviewDeliveryClaim claim = claimed.orElseThrow();

        if (!intent.equals(claim.intent())) {
            throw new IllegalStateException(
                    "delivery claim conflicts with durable intent");
        }
        if (!eligibility.test(intent)) {
            ReviewDeliveryOutcome stale = new ReviewDeliveryOutcome(
                    ReviewDeliveryState.STALE,
                    intent.intentId(),
                    intent.idempotencyKey(),
                    claim.attemptNumber(),
                    "stale_head",
                    null);
            return recordExact(claim, stale, now);
        }

        ReviewDeliveryOutcome effectStarted = Objects.requireNonNull(
                outbox.markEffectStarted(claim, now),
                "markEffectStarted returned null");
        requireEffectStart(claim, effectStarted);

        ReviewDeliveryOutcome proposed = Objects.requireNonNull(
                gateway.deliver(claim),
                "delivery gateway returned null");
        requireGatewayOutcome(claim, proposed);
        return recordExact(claim, proposed, now);
    }

    private ReviewDeliveryOutcome recordExact(
            ReviewDeliveryClaim claim,
            ReviewDeliveryOutcome proposed,
            Instant now) {
        ReviewDeliveryOutcome stored = Objects.requireNonNull(
                outbox.recordOutcome(claim, proposed, now),
                "recordOutcome returned null");
        if (!proposed.equals(stored)) {
            throw new IllegalStateException(
                    "stored delivery outcome conflicts with exact attempt result");
        }
        return stored;
    }

    private static void requireGatewayOutcome(
            ReviewDeliveryClaim claim,
            ReviewDeliveryOutcome outcome) {
        if (outcome.state() != ReviewDeliveryState.DELIVERED
                && outcome.state() != ReviewDeliveryState.RETRYABLE_FAILED
                && outcome.state() != ReviewDeliveryState.PERMANENT_FAILED
                && outcome.state() != ReviewDeliveryState.AMBIGUOUS) {
            throw new IllegalArgumentException(
                    "delivery gateway returned an invalid outcome state");
        }
        if (!claim.intent().intentId().equals(outcome.intentId())
                || !claim.intent().idempotencyKey().equals(
                        outcome.idempotencyKey())
                || claim.attemptNumber() != outcome.attemptCount()) {
            throw new IllegalArgumentException(
                    "delivery gateway outcome conflicts with claimed identity");
        }
    }

    private static void requireEffectStart(
            ReviewDeliveryClaim claim,
            ReviewDeliveryOutcome outcome) {
        if (outcome.state() != ReviewDeliveryState.AMBIGUOUS
                || !"provider_ack_unknown".equals(outcome.reasonCode())
                || !claim.intent().intentId().equals(outcome.intentId())
                || !claim.intent().idempotencyKey().equals(
                        outcome.idempotencyKey())
                || claim.attemptNumber() != outcome.attemptCount()) {
            throw new IllegalStateException(
                    "durable delivery effect start conflicts with claimed identity");
        }
    }

    private static boolean terminalNoOp(ReviewDeliveryOutcome outcome) {
        return outcome.state() == ReviewDeliveryState.DELIVERED
                || outcome.state() == ReviewDeliveryState.STALE
                || outcome.state() == ReviewDeliveryState.PERMANENT_FAILED
                || outcome.state() == ReviewDeliveryState.AMBIGUOUS;
    }

    private static void requireIntentId(String intentId) {
        if (intentId == null || intentId.isBlank()) {
            throw new IllegalArgumentException("intentId must not be blank");
        }
    }

}
