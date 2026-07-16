package org.rostilos.codecrow.analysisengine.delivery;

/** Independent lifecycle state for one durable delivery intent. */
public enum ReviewDeliveryState {
    PENDING,
    IN_FLIGHT,
    RETRYABLE_FAILED,
    PERMANENT_FAILED,
    AMBIGUOUS,
    DELIVERED,
    STALE
}
