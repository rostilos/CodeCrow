package org.rostilos.codecrow.analysisengine.policy;

/** Result of the fail-closed publication boundary. */
public enum PublicationReservation {
    RESERVED,
    DUPLICATE,
    STALE_HEAD,
    SHADOW_DENIED
}
