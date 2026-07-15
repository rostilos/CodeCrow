package org.rostilos.codecrow.analysisengine.policy;

import java.time.Instant;
import java.util.Objects;
import java.util.regex.Pattern;

/** Persistable artifact envelope kept outside the legacy final-result cache. */
public record ExecutionArtifact(
        String executionId,
        ArtifactNamespace namespace,
        String artifactId,
        String payloadJson,
        Instant createdAt) {
    private static final Pattern IDENTIFIER = Pattern.compile("[A-Za-z0-9._:-]{1,160}");
    private static final int MAX_PAYLOAD_BYTES = 4 * 1024 * 1024;

    public ExecutionArtifact {
        if (executionId == null || !IDENTIFIER.matcher(executionId).matches()) {
            throw new IllegalArgumentException("executionId is invalid");
        }
        Objects.requireNonNull(namespace, "namespace");
        if (artifactId == null || !IDENTIFIER.matcher(artifactId).matches()) {
            throw new IllegalArgumentException("artifactId is invalid");
        }
        Objects.requireNonNull(payloadJson, "payloadJson");
        Objects.requireNonNull(createdAt, "createdAt");
        if (payloadJson.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > MAX_PAYLOAD_BYTES) {
            throw new IllegalArgumentException("artifact payload exceeds the bounded scaffold limit");
        }
    }
}
