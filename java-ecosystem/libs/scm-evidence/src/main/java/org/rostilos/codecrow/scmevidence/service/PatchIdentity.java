package org.rostilos.codecrow.scmevidence.service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/** Stable identity for a patch across merge commits and cherry-picks. */
public final class PatchIdentity {
    private PatchIdentity() {}

    public static String sha256(String unifiedDiff) {
        StringBuilder normalized = new StringBuilder();
        if (unifiedDiff != null) {
            for (String raw : unifiedDiff.replace("\r\n", "\n").split("\n", -1)) {
                String line = stripTrailingWhitespace(raw);
                if (line.startsWith("diff --git ")
                        || line.startsWith("rename from ")
                        || line.startsWith("rename to ")
                        || line.startsWith("new file mode ")
                        || line.startsWith("deleted file mode ")
                        || (line.startsWith("+") && !line.startsWith("+++"))
                        || (line.startsWith("-") && !line.startsWith("---"))) {
                    normalized.append(line).append('\n');
                }
            }
        }
        return digest(normalized.toString());
    }

    public static String lineSha256(String line) {
        return digest(line == null ? "" : line.strip());
    }

    static String digest(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 unavailable", impossible);
        }
    }

    private static String stripTrailingWhitespace(String line) {
        int end = line.length();
        while (end > 0 && Character.isWhitespace(line.charAt(end - 1))) {
            end--;
        }
        return line.substring(0, end);
    }
}
