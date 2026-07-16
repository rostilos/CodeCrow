package org.rostilos.codecrow.analysisengine.delivery;

/** Provider failure classification based on whether an effect may be retried safely. */
public enum ReviewDeliveryFailureDisposition {
    RETRYABLE,
    PERMANENT,
    AMBIGUOUS
}
