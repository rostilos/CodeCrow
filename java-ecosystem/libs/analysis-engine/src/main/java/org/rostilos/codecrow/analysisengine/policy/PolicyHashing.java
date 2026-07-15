package org.rostilos.codecrow.analysisengine.policy;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/** Centralized deterministic hashing for policy identities and fences. */
public final class PolicyHashing {
    private PolicyHashing() {
    }

    public static String sha256(String value) {
        return digestHex("SHA-256", value);
    }

    static String digestHex(String algorithm, String value) {
        try {
            byte[] digest = MessageDigest.getInstance(algorithm)
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(algorithm + " is unavailable", error);
        }
    }
}
