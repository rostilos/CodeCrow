package org.rostilos.codecrow.analysisengine.execution;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Objects;
import java.util.Arrays;

/** Immutable artifact metadata plus the exact bytes durably stored for it. */
public record ExecutionArtifactPayload(
        ArtifactManifestEntry entry,
        byte[] content) {

    public ExecutionArtifactPayload {
        Objects.requireNonNull(entry, "entry");
        Objects.requireNonNull(content, "content");
        content = content.clone();
        if (content.length != entry.byteLength()) {
            throw new IllegalArgumentException(
                    "artifact byte length does not match its manifest entry");
        }
        String observedDigest = sha256(content);
        if (!MessageDigest.isEqual(
                observedDigest.getBytes(java.nio.charset.StandardCharsets.US_ASCII),
                entry.contentDigest().getBytes(java.nio.charset.StandardCharsets.US_ASCII))) {
            throw new IllegalArgumentException(
                    "artifact content digest does not match its manifest entry");
        }
    }

    @Override
    public byte[] content() {
        return content.clone();
    }

    @Override
    public boolean equals(Object other) {
        return other instanceof ExecutionArtifactPayload payload
                && entry.equals(payload.entry)
                && Arrays.equals(content, payload.content);
    }

    @Override
    public int hashCode() {
        return 31 * entry.hashCode() + Arrays.hashCode(content);
    }

    private static String sha256(byte[] value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }
}
