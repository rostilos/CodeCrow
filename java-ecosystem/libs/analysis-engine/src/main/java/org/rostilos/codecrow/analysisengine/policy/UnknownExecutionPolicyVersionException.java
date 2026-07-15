package org.rostilos.codecrow.analysisengine.policy;

public final class UnknownExecutionPolicyVersionException extends IllegalArgumentException {
    public UnknownExecutionPolicyVersionException(String version) {
        super("Unknown execution policy version: " + version);
    }
}
