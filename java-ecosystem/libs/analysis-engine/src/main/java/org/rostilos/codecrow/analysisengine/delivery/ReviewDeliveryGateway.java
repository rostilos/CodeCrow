package org.rostilos.codecrow.analysisengine.delivery;

/** Idempotent provider boundary; implementations must honor the intent key. */
@FunctionalInterface
public interface ReviewDeliveryGateway {

    ReviewDeliveryOutcome deliver(ReviewDeliveryClaim claim);
}
