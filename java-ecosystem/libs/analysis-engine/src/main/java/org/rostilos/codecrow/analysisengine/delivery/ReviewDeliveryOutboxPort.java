package org.rostilos.codecrow.analysisengine.delivery;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

/** Durable create/load, claim, and outcome boundary for delivery intents. */
public interface ReviewDeliveryOutboxPort {

    ReviewDeliveryHead registerCurrentHead(ReviewDeliveryHead proposed);

    Optional<ReviewDeliveryIntent> createOrLoadIfCurrent(
            ReviewDeliveryIntent proposed);

    Optional<ReviewDeliveryIntent> findIntent(String intentId);

    Optional<ReviewDeliveryClaim> tryClaim(String intentId, Instant now);

    ReviewDeliveryOutcome markEffectStarted(
            ReviewDeliveryClaim claim,
            Instant now);

    ReviewDeliveryOutcome recordOutcome(
            ReviewDeliveryClaim claim,
            ReviewDeliveryOutcome outcome,
            Instant now);

    Optional<ReviewDeliveryOutcome> findOutcome(String intentId);

    /** Compatibility-safe worker discovery hook until a durable adapter supplies it. */
    default List<String> findDueIntentIds(Instant now, int limit) {
        return List.of();
    }
}
