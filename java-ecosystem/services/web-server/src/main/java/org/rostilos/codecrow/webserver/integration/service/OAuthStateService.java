package org.rostilos.codecrow.webserver.integration.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.InvalidKeyException;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;

/**
 * Service for secure OAuth state generation and validation.
 * Uses HMAC-SHA256 to sign state tokens, preventing state forgery attacks.
 * 
 * State format: Base64(providerId:workspaceId:timestamp:nonce:signature)
 * The signature is HMAC-SHA256(providerId:workspaceId:timestamp:nonce, secret)
 */
@Service
public class OAuthStateService {
    
    private static final Logger log = LoggerFactory.getLogger(OAuthStateService.class);
    private static final String HMAC_ALGORITHM = "HmacSHA256";
    private static final String DELIMITER = ":";
    private static final long STATE_EXPIRATION_MS = 10 * 60 * 1000; // 10 minutes
    
    private final SecureRandom secureRandom = new SecureRandom();
    
    @Value("${codecrow.security.jwtSecret}")
    private String secretKey;
    
    /**
     * Generate a secure signed OAuth state token.
     * 
     * @param providerId The VCS provider ID
     * @param workspaceId The workspace ID
     * @return Base64-encoded signed state token
     */
    public String generateState(String providerId, Long workspaceId) {
        return generateState(providerId, workspaceId, null);
    }
    
    /**
     * Generate a secure signed OAuth state token with optional connection ID for reconnection.
     * 
     * @param providerId The VCS provider ID
     * @param workspaceId The workspace ID
     * @param connectionId Optional connection ID for reconnection flow (null for new connections)
     * @return Base64-encoded signed state token
     */
    public String generateState(String providerId, Long workspaceId, Long connectionId) {
        long timestamp = System.currentTimeMillis();
        String nonce = generateNonce();
        String connIdStr = connectionId != null ? connectionId.toString() : "0";
        
        String payload = providerId + DELIMITER + workspaceId + DELIMITER + timestamp + DELIMITER + nonce + DELIMITER + connIdStr;
        String signature = computeHmac(payload);
        
        String state = payload + DELIMITER + signature;
        return Base64.getUrlEncoder().withoutPadding().encodeToString(state.getBytes(StandardCharsets.UTF_8));
    }
    
    /**
     * Validate and extract workspace ID from a signed OAuth state token.
     * 
     * @param state The Base64-encoded state token
     * @return The validated workspace ID, or null if validation fails
     */
    public Long validateAndExtractWorkspaceId(String state) {
        OAuthStateData data = validateAndExtractState(state);
        return data != null ? data.workspaceId() : null;
    }
    
    /**
     * Validate and extract both workspace ID and optional connection ID from a signed OAuth state token.
     * 
     * @param state The Base64-encoded state token
     * @return OAuthStateData containing workspaceId and optional connectionId, or null if validation fails
     */
    public OAuthStateData validateAndExtractState(String state) {
        if (state == null || state.isEmpty()) {
            log.warn("OAuth state is null or empty");
            return null;
        }
        
        try {
            String decoded = new String(Base64.getUrlDecoder().decode(state), StandardCharsets.UTF_8);
            String[] parts = decoded.split(DELIMITER);
            
            // Support both old format (5 parts) and new format (6 parts with connectionId)
            if (parts.length != 5 && parts.length != 6) {
                log.warn("Invalid OAuth state format: expected 5 or 6 parts, got {}", parts.length);
                return null;
            }
            
            String providerId = parts[0];
            String workspaceIdStr = parts[1];
            String timestampStr = parts[2];
            String nonce = parts[3];
            String connIdStr = parts.length == 6 ? parts[4] : "0";
            String receivedSignature = parts.length == 6 ? parts[5] : parts[4];
            
            // Reconstruct payload for signature verification
            String payload = parts.length == 6
                ? providerId + DELIMITER + workspaceIdStr + DELIMITER + timestampStr + DELIMITER + nonce + DELIMITER + connIdStr
                : providerId + DELIMITER + workspaceIdStr + DELIMITER + timestampStr + DELIMITER + nonce;
            String expectedSignature = computeHmac(payload);
            
            if (!constantTimeEquals(expectedSignature, receivedSignature)) {
                log.warn("OAuth state signature validation failed - possible forgery attempt");
                return null;
            }
            
            long timestamp = Long.parseLong(timestampStr);
            long now = System.currentTimeMillis();
            if (now - timestamp > STATE_EXPIRATION_MS) {
                log.warn("OAuth state expired: issued {}ms ago", now - timestamp);
                return null;
            }
            
            Long workspaceId = Long.parseLong(workspaceIdStr);
            Long connectionId = "0".equals(connIdStr) ? null : Long.parseLong(connIdStr);
            log.debug("OAuth state validated successfully for workspace {} (connectionId: {})", workspaceId, connectionId);
            return new OAuthStateData(workspaceId, connectionId);
            
        } catch (IllegalArgumentException e) {
            log.warn("Failed to decode or parse OAuth state: {}", e.getMessage());
            return null;
        } catch (Exception e) {
            log.error("Unexpected error validating OAuth state", e);
            return null;
        }
    }
    
    /**
     * Data extracted from a validated OAuth state token.
     */
    public record OAuthStateData(Long workspaceId, Long connectionId) {
        public boolean isReconnect() {
            return connectionId != null;
        }
    }
    
    private String generateNonce() {
        byte[] nonceBytes = new byte[16];
        secureRandom.nextBytes(nonceBytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(nonceBytes);
    }
    
    /**
     * Compute HMAC-SHA256 signature for the given data.
     */
    private String computeHmac(String data) {
        try {
            Mac mac = Mac.getInstance(HMAC_ALGORITHM);
            SecretKeySpec keySpec = new SecretKeySpec(
                    secretKey.getBytes(StandardCharsets.UTF_8), 
                    HMAC_ALGORITHM
            );
            mac.init(keySpec);
            byte[] hmacBytes = mac.doFinal(data.getBytes(StandardCharsets.UTF_8));
            return Base64.getUrlEncoder().withoutPadding().encodeToString(hmacBytes);
        } catch (NoSuchAlgorithmException | InvalidKeyException e) {
            throw new RuntimeException("Failed to compute HMAC", e);
        }
    }
    
    /**
     * Constant-time string comparison to prevent timing attacks.
     */
    private boolean constantTimeEquals(String a, String b) {
        if (a == null || b == null) {
            return false;
        }
        if (a.length() != b.length()) {
            return false;
        }
        int result = 0;
        for (int i = 0; i < a.length(); i++) {
            result |= a.charAt(i) ^ b.charAt(i);
        }
        return result == 0;
    }
}
