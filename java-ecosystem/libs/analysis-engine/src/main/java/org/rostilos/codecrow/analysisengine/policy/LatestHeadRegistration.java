package org.rostilos.codecrow.analysisengine.policy;

/** Result of installing one exact, provider-verified PR head as desired work. */
public enum LatestHeadRegistration {
    ACCEPTED,
    DUPLICATE,
    SUPERSEDED
}
