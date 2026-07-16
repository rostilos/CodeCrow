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
 * Current state format:
 * Base64(providerId:workspaceId:timestamp:nonce:connectionId:installationId:purpose:signature).
 * The optional IDs use {@code 0}; state tokens issued in older formats remain
 * valid until their normal expiration.
 */
@Service
public class OAuthStateService {

    public static final String GITHUB_INSTALL_START = "github-install-start";
    public static final String GITHUB_INSTALL_SELECT = "github-install-select";
    public static final String GITHUB_INSTALL_VERIFY = "github-install-verify";
    
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
        return generateState(providerId, workspaceId, null, null);
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
        return generateState(providerId, workspaceId, connectionId, null);
    }

    /**
     * Generate signed state that also carries an untrusted GitHub installation
     * ID through the user-authorization round trip. Keeping this value in state
     * avoids reserving an installation in the database before GitHub proves the
     * user can access it.
     */
    public String generateState(
            String providerId,
            Long workspaceId,
            Long connectionId,
            Long installationId) {
        return generateState(providerId, workspaceId, connectionId, installationId, null);
    }

    public String generateState(
            String providerId,
            Long workspaceId,
            Long connectionId,
            Long installationId,
            String purpose) {
        long timestamp = System.currentTimeMillis();
        String nonce = generateNonce();
        String connIdStr = connectionId != null ? connectionId.toString() : "0";
        String installationIdStr = installationId != null ? installationId.toString() : "0";
        
        String payload = providerId + DELIMITER + workspaceId + DELIMITER + timestamp + DELIMITER
                + nonce + DELIMITER + connIdStr + DELIMITER + installationIdStr;
        if (purpose != null && !purpose.isBlank()) {
            if (purpose.contains(DELIMITER)) {
                throw new IllegalArgumentException("OAuth state purpose contains an invalid character");
            }
            payload += DELIMITER + purpose;
        }
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
            
            // Accept legacy formats until their normal expiration:
            // 5 parts: no connection ID, 6: connection ID, 7: connection +
            // installation IDs, 8: current format with an explicit purpose.
            if (parts.length < 5 || parts.length > 8) {
                log.warn("Invalid OAuth state format: expected 5 to 8 parts, got {}", parts.length);
                return null;
            }
            
            String providerId = parts[0];
            String workspaceIdStr = parts[1];
            String timestampStr = parts[2];
            String nonce = parts[3];
            String connIdStr = parts.length >= 6 ? parts[4] : "0";
            String installationIdStr = parts.length >= 7 ? parts[5] : "0";
            String purpose = parts.length == 8 ? parts[6] : null;
            String receivedSignature = parts[parts.length - 1];
            
            // Reconstruct payload for signature verification
            String payload = String.join(
                    DELIMITER,
                    java.util.Arrays.copyOf(parts, parts.length - 1));
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
            Long installationId = "0".equals(installationIdStr)
                    ? null
                    : Long.parseLong(installationIdStr);
            log.debug("OAuth state validated successfully for workspace {} " +
                            "(connectionId: {}, installationId: {}, purpose: {})",
                    workspaceId, connectionId, installationId, purpose);
            return new OAuthStateData(providerId, workspaceId, connectionId, installationId, purpose);
            
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
    public record OAuthStateData(
            String providerId,
            Long workspaceId,
            Long connectionId,
            Long installationId,
            String purpose) {
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
