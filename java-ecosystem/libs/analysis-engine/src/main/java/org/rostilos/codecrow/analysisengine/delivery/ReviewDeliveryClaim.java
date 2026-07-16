package org.rostilos.codecrow.analysisengine.delivery;

import java.util.Objects;

/** One atomically leased attempt for a durable delivery intent. */
public record ReviewDeliveryClaim(
        ReviewDeliveryIntent intent,
        int attemptNumber,
        String leaseToken) {

    public ReviewDeliveryClaim {
        intent = Objects.requireNonNull(intent, "intent");
        if (attemptNumber <= 0) {
            throw new IllegalArgumentException("attemptNumber must be positive");
        }
        if (leaseToken == null
                || leaseToken.isBlank()
                || leaseToken.length() > 160
                || leaseToken.indexOf('\0') >= 0) {
            throw new IllegalArgumentException("leaseToken is invalid");
        }
    }
}
