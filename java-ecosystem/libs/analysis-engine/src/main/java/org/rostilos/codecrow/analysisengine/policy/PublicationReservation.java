package org.rostilos.codecrow.analysisengine.policy;

/** Result of the fail-closed publication boundary. */
public enum PublicationReservation {
    RESERVED,
    DUPLICATE,
    SHADOW_DENIED
}
