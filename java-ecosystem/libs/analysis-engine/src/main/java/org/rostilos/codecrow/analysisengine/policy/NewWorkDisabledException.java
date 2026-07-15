package org.rostilos.codecrow.analysisengine.policy;

public final class NewWorkDisabledException extends IllegalStateException {
    public NewWorkDisabledException(String configRevision) {
        super("New analysis work is disabled by policy config " + configRevision);
    }
}
