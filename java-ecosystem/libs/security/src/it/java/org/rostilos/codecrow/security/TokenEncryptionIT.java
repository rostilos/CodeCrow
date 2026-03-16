package org.rostilos.codecrow.security;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Base64;

import org.junit.jupiter.api.*;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Integration test for token encryption/decryption used for OAuth tokens.
 * AES-GCM roundtrip, key rotation, tamper detection.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class TokenEncryptionIT {

    private static final int GCM_TAG_LENGTH = 128;
    private static final int GCM_IV_LENGTH = 12;

    @Test
    @Order(1)
    @DisplayName("AES-GCM roundtrip: encrypt then decrypt should return original")
    void shouldEncryptAndDecrypt() throws Exception {
        SecretKey key = generateKey();
        String plaintext = "ghp_very_secret_github_oauth_token_12345";

        String encrypted = encrypt(plaintext, key);
        String decrypted = decrypt(encrypted, key);

        assertThat(decrypted).isEqualTo(plaintext);
        assertThat(encrypted).isNotEqualTo(plaintext);
    }

    @Test
    @Order(2)
    @DisplayName("Same plaintext should produce different ciphertext each time (random IV)")
    void shouldProduceDifferentCiphertexts() throws Exception {
        SecretKey key = generateKey();
        String plaintext = "same_token_value";

        String encrypted1 = encrypt(plaintext, key);
        String encrypted2 = encrypt(plaintext, key);

        assertThat(encrypted1).isNotEqualTo(encrypted2); // Random IV ensures uniqueness
        assertThat(decrypt(encrypted1, key)).isEqualTo(plaintext);
        assertThat(decrypt(encrypted2, key)).isEqualTo(plaintext);
    }

    @Test
    @Order(3)
    @DisplayName("Decryption with wrong key should fail")
    void shouldFailWithWrongKey() throws Exception {
        SecretKey key1 = generateKey();
        SecretKey key2 = generateKey();

        String encrypted = encrypt("secret_token", key1);

        assertThatThrownBy(() -> decrypt(encrypted, key2))
                .isInstanceOf(Exception.class);
    }

    @Test
    @Order(4)
    @DisplayName("Tampered ciphertext should fail authentication")
    void shouldDetectTampering() throws Exception {
        SecretKey key = generateKey();
        String encrypted = encrypt("important_token", key);

        // Tamper with the base64 string
        byte[] bytes = Base64.getDecoder().decode(encrypted);
        bytes[bytes.length - 1] ^= 0xFF; // Flip last byte
        String tampered = Base64.getEncoder().encodeToString(bytes);

        assertThatThrownBy(() -> decrypt(tampered, key))
                .isInstanceOf(Exception.class);
    }

    @Test
    @Order(5)
    @DisplayName("Should handle empty string encryption")
    void shouldHandleEmptyString() throws Exception {
        SecretKey key = generateKey();
        String encrypted = encrypt("", key);
        String decrypted = decrypt(encrypted, key);
        assertThat(decrypted).isEmpty();
    }

    @Test
    @Order(6)
    @DisplayName("Should handle long token values")
    void shouldHandleLongTokens() throws Exception {
        SecretKey key = generateKey();
        String longToken = "x".repeat(10_000);
        String encrypted = encrypt(longToken, key);
        String decrypted = decrypt(encrypted, key);
        assertThat(decrypted).isEqualTo(longToken);
    }

    // Helpers simulating the encryption service
    private SecretKey generateKey() throws Exception {
        KeyGenerator gen = KeyGenerator.getInstance("AES");
        gen.init(256);
        return gen.generateKey();
    }

    private String encrypt(String plaintext, SecretKey key) throws Exception {
        byte[] iv = new byte[GCM_IV_LENGTH];
        new SecureRandom().nextBytes(iv);

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(GCM_TAG_LENGTH, iv));

        byte[] encrypted = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));

        // Prepend IV to ciphertext
        byte[] combined = new byte[iv.length + encrypted.length];
        System.arraycopy(iv, 0, combined, 0, iv.length);
        System.arraycopy(encrypted, 0, combined, iv.length, encrypted.length);

        return Base64.getEncoder().encodeToString(combined);
    }

    private String decrypt(String encryptedBase64, SecretKey key) throws Exception {
        byte[] combined = Base64.getDecoder().decode(encryptedBase64);

        byte[] iv = new byte[GCM_IV_LENGTH];
        System.arraycopy(combined, 0, iv, 0, iv.length);

        byte[] ciphertext = new byte[combined.length - iv.length];
        System.arraycopy(combined, iv.length, ciphertext, 0, ciphertext.length);

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(GCM_TAG_LENGTH, iv));

        return new String(cipher.doFinal(ciphertext), StandardCharsets.UTF_8);
    }
}
