package org.rostilos.codecrow.analysisengine.util;

import com.knuddels.jtokkit.Encodings;
import com.knuddels.jtokkit.api.Encoding;
import com.knuddels.jtokkit.api.EncodingRegistry;
import com.knuddels.jtokkit.api.EncodingType;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Utility class for estimating token counts in text content.
 * Uses the cl100k_base encoding (used by GPT-4, Claude, and most modern LLMs).
 */
public final class TokenEstimator {
    private static final Logger log = LoggerFactory.getLogger(TokenEstimator.class);
    
    private static final EncodingRegistry ENCODING_REGISTRY = Encodings.newDefaultEncodingRegistry();
    private static final Encoding ENCODING = ENCODING_REGISTRY.getEncoding(EncodingType.CL100K_BASE);
    
    // Prevent instantiation
    private TokenEstimator() {
        throw new UnsupportedOperationException("Utility class");
    }
    
    /**
     * Estimate the number of tokens in the given text.
     * 
     * @param text The text to estimate tokens for
     * @return The estimated token count, or 0 if text is null/empty
     */
    public static int estimateTokens(String text) {
        if (text == null || text.isEmpty()) {
            return 0;
        }
        try {
            return ENCODING.countTokens(text);
        } catch (Exception e) {
            log.warn("Failed to count tokens, using fallback estimation: {}", e.getMessage());
            // Fallback: rough estimate of ~4 characters per token
            return text.length() / 4;
        }
    }
    
    /**
     * Check if the estimated token count exceeds the given limit.
     * 
     * @param text The text to check
     * @param maxTokens The maximum allowed tokens
     * @return true if the text exceeds the limit, false otherwise
     */
    public static boolean exceedsLimit(String text, int maxTokens) {
        return estimateTokens(text) > maxTokens;
    }
    
    /**
     * Result of a token estimation check with details.
     */
    public record TokenEstimationResult(
        int estimatedTokens,
        int maxAllowedTokens,
        boolean exceedsLimit,
        double utilizationPercentage
    ) {
        public String toLogString() {
            return String.format("Tokens: %d / %d (%.1f%%) - %s",
                estimatedTokens, maxAllowedTokens, utilizationPercentage,
                exceedsLimit ? "EXCEEDS LIMIT" : "within limit");
        }
    }
    
    /**
     * Estimate tokens and check against limit, returning detailed result.
     * 
     * @param text The text to check
     * @param maxTokens The maximum allowed tokens
     * @return Detailed estimation result
     */
    public static TokenEstimationResult estimateAndCheck(String text, int maxTokens) {
        int estimated = estimateTokens(text);
        double utilization = maxTokens > 0 ? (estimated * 100.0 / maxTokens) : 0;
        return new TokenEstimationResult(
            estimated,
            maxTokens,
            estimated > maxTokens,
            utilization
        );
    }
}
