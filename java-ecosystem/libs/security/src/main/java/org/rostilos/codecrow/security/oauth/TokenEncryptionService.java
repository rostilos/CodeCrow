package org.rostilos.codecrow.security.oauth;

/**
 * Backward-compatible subclass that delegates to the canonical implementation
 * in {@code libs/core}. All logic lives in
 * {@link org.rostilos.codecrow.core.security.TokenEncryptionService}.
 * <p>
 * Existing {@code @Bean} definitions and consumers that import from
 * {@code security.oauth.TokenEncryptionService} continue to work unchanged.
 */
public class TokenEncryptionService extends org.rostilos.codecrow.core.security.TokenEncryptionService {

    public TokenEncryptionService(String base64CurrentKey, String base64OldKey) {
        super(base64CurrentKey, base64OldKey);
    }
}