package org.rostilos.codecrow.mcp.util;

import com.knuddels.jtokkit.Encodings;
import com.knuddels.jtokkit.api.Encoding;
import com.knuddels.jtokkit.api.EncodingRegistry;
import com.knuddels.jtokkit.api.EncodingType;

public class TokenLimitGuard {
    private static final int DEFAULT_LIMIT = 200000;
    /**
     * Returns the per-request max allowed tokens if provided via -Dmax.allowed.tokens
     * or null if the property isn't set or is invalid.
     */
    private static Integer getMaxAllowedTokens() {
        String prop = System.getProperty("max.allowed.tokens");
        if (prop == null || prop.isEmpty()) {
            return null;
        }
        try {
            return Integer.valueOf(prop);
        } catch (NumberFormatException e) {
            //TODO: logger
            System.err.println("Invalid max.allowed.tokens value: " + prop);
            return null;
        }
    }

    /**
     * Conservative token estimate: count UTF-8 bytes and divide by 4.
     * Ensures at least 1 token for non-empty strings.
     */
    private static int estimateTokensForText(String text) {
        EncodingRegistry encodingRegistry = Encodings.newDefaultEncodingRegistry();
        Encoding encoding = encodingRegistry.getEncoding(EncodingType.CL100K_BASE);
        return encoding.countTokens(text);
    }
    
    public static boolean isExceededMaxAllowedTokens(String text) {
        Integer maxAllowedTokens = getMaxAllowedTokens();
        int estimatedTokens = estimateTokensForText(text);
        return estimatedTokens > ( maxAllowedTokens != null ? maxAllowedTokens : DEFAULT_LIMIT);
    } 
    
}
