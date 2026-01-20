package org.rostilos.codecrow.security.oauth;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.security.GeneralSecurityException;
import java.util.Base64;
import javax.crypto.KeyGenerator;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("TokenEncryptionService")
class TokenEncryptionServiceTest {

    private static final String TEST_KEY_1;
    private static final String TEST_KEY_2;
    
    static {
        // Generate valid AES-256 keys for testing
        try {
            KeyGenerator keyGen = KeyGenerator.getInstance("AES");
            keyGen.init(256);
            TEST_KEY_1 = Base64.getEncoder().encodeToString(keyGen.generateKey().getEncoded());
            TEST_KEY_2 = Base64.getEncoder().encodeToString(keyGen.generateKey().getEncoded());
        } catch (Exception e) {
            throw new RuntimeException("Failed to generate test keys", e);
        }
    }

    private TokenEncryptionService service;

    @BeforeEach
    void setUp() {
        service = new TokenEncryptionService(TEST_KEY_1, null);
    }

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should create service with current key only")
        void shouldCreateServiceWithCurrentKeyOnly() {
            TokenEncryptionService service = new TokenEncryptionService(TEST_KEY_1, null);
            assertThat(service).isNotNull();
        }

        @Test
        @DisplayName("should create service with both keys")
        void shouldCreateServiceWithBothKeys() {
            TokenEncryptionService service = new TokenEncryptionService(TEST_KEY_1, TEST_KEY_2);
            assertThat(service).isNotNull();
        }

        @Test
        @DisplayName("should handle empty old key")
        void shouldHandleEmptyOldKey() {
            TokenEncryptionService service = new TokenEncryptionService(TEST_KEY_1, "");
            assertThat(service).isNotNull();
        }
    }

    @Nested
    @DisplayName("encrypt()")
    class EncryptTests {

        @Test
        @DisplayName("should encrypt plaintext to base64 string")
        void shouldEncryptPlaintextToBase64() throws GeneralSecurityException {
            String plaintext = "my-secret-token";
            
            String encrypted = service.encrypt(plaintext);
            
            assertThat(encrypted).isNotNull();
            assertThat(encrypted).isNotEqualTo(plaintext);
            // Should be valid base64
            assertThat(Base64.getDecoder().decode(encrypted)).isNotEmpty();
        }

        @Test
        @DisplayName("should produce different ciphertext for same plaintext (random IV)")
        void shouldProduceDifferentCiphertextForSamePlaintext() throws GeneralSecurityException {
            String plaintext = "my-secret-token";
            
            String encrypted1 = service.encrypt(plaintext);
            String encrypted2 = service.encrypt(plaintext);
            
            // Due to random IV, same plaintext should produce different ciphertext
            assertThat(encrypted1).isNotEqualTo(encrypted2);
        }

        @Test
        @DisplayName("should handle empty string")
        void shouldHandleEmptyString() throws GeneralSecurityException {
            String encrypted = service.encrypt("");
            
            assertThat(encrypted).isNotNull();
            assertThat(encrypted).isNotEmpty();
        }

        @Test
        @DisplayName("should handle special characters")
        void shouldHandleSpecialCharacters() throws GeneralSecurityException {
            String plaintext = "token!@#$%^&*()_+=[]{}|;':\",./<>?`~";
            
            String encrypted = service.encrypt(plaintext);
            
            assertThat(encrypted).isNotNull();
        }

        @Test
        @DisplayName("should handle long tokens")
        void shouldHandleLongTokens() throws GeneralSecurityException {
            String plaintext = "a".repeat(10000);
            
            String encrypted = service.encrypt(plaintext);
            
            assertThat(encrypted).isNotNull();
        }
    }

    @Nested
    @DisplayName("decrypt()")
    class DecryptTests {

        @Test
        @DisplayName("should decrypt to original plaintext")
        void shouldDecryptToOriginalPlaintext() throws GeneralSecurityException {
            String plaintext = "my-secret-token-12345";
            String encrypted = service.encrypt(plaintext);
            
            String decrypted = service.decrypt(encrypted);
            
            assertThat(decrypted).isEqualTo(plaintext);
        }

        @Test
        @DisplayName("should decrypt empty string")
        void shouldDecryptEmptyString() throws GeneralSecurityException {
            String plaintext = "";
            String encrypted = service.encrypt(plaintext);
            
            String decrypted = service.decrypt(encrypted);
            
            assertThat(decrypted).isEqualTo(plaintext);
        }

        @Test
        @DisplayName("should decrypt special characters")
        void shouldDecryptSpecialCharacters() throws GeneralSecurityException {
            String plaintext = "token!@#$%^&*()_+=[]{}|;':\",./<>?`~";
            String encrypted = service.encrypt(plaintext);
            
            String decrypted = service.decrypt(encrypted);
            
            assertThat(decrypted).isEqualTo(plaintext);
        }

        @Test
        @DisplayName("should decrypt long tokens")
        void shouldDecryptLongTokens() throws GeneralSecurityException {
            String plaintext = "a".repeat(10000);
            String encrypted = service.encrypt(plaintext);
            
            String decrypted = service.decrypt(encrypted);
            
            assertThat(decrypted).isEqualTo(plaintext);
        }

        @Test
        @DisplayName("should throw exception for invalid ciphertext")
        void shouldThrowExceptionForInvalidCiphertext() {
            String invalidCiphertext = Base64.getEncoder().encodeToString("invalid".getBytes());
            
            assertThatThrownBy(() -> service.decrypt(invalidCiphertext))
                .isInstanceOf(Exception.class);
        }

        @Test
        @DisplayName("should throw exception for corrupted base64")
        void shouldThrowExceptionForCorruptedBase64() {
            String corruptedBase64 = "not-valid-base64!!!";
            
            assertThatThrownBy(() -> service.decrypt(corruptedBase64))
                .isInstanceOf(IllegalArgumentException.class);
        }
    }

    @Nested
    @DisplayName("Key rotation")
    class KeyRotationTests {

        @Test
        @DisplayName("should decrypt with old key when current key fails")
        void shouldDecryptWithOldKeyWhenCurrentFails() throws GeneralSecurityException {
            // Encrypt with key2
            TokenEncryptionService oldService = new TokenEncryptionService(TEST_KEY_2, null);
            String encrypted = oldService.encrypt("secret-data");
            
            // Create new service with key1 as current and key2 as old
            TokenEncryptionService newService = new TokenEncryptionService(TEST_KEY_1, TEST_KEY_2);
            
            // Should successfully decrypt using old key
            String decrypted = newService.decrypt(encrypted);
            
            assertThat(decrypted).isEqualTo("secret-data");
        }

        @Test
        @DisplayName("should always encrypt with current key")
        void shouldAlwaysEncryptWithCurrentKey() throws GeneralSecurityException {
            TokenEncryptionService serviceWithBothKeys = new TokenEncryptionService(TEST_KEY_1, TEST_KEY_2);
            String encrypted = serviceWithBothKeys.encrypt("test-data");
            
            // Should be decryptable with current key only
            TokenEncryptionService currentKeyOnly = new TokenEncryptionService(TEST_KEY_1, null);
            String decrypted = currentKeyOnly.decrypt(encrypted);
            
            assertThat(decrypted).isEqualTo("test-data");
        }

        @Test
        @DisplayName("should fail when neither key can decrypt")
        void shouldFailWhenNeitherKeyCanDecrypt() throws GeneralSecurityException {
            // Generate a third key
            String thirdKey;
            try {
                KeyGenerator keyGen = KeyGenerator.getInstance("AES");
                keyGen.init(256);
                thirdKey = Base64.getEncoder().encodeToString(keyGen.generateKey().getEncoded());
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
            
            // Encrypt with third key
            TokenEncryptionService thirdKeyService = new TokenEncryptionService(thirdKey, null);
            String encrypted = thirdKeyService.encrypt("secret");
            
            // Try to decrypt with service that doesn't have third key
            TokenEncryptionService service = new TokenEncryptionService(TEST_KEY_1, TEST_KEY_2);
            
            assertThatThrownBy(() -> service.decrypt(encrypted))
                .isInstanceOf(GeneralSecurityException.class);
        }
    }

    @Nested
    @DisplayName("Unicode support")
    class UnicodeSupportTests {

        @Test
        @DisplayName("should handle Unicode characters")
        void shouldHandleUnicodeCharacters() throws GeneralSecurityException {
            String plaintext = "用户令牌-üñíçödé-🔐🎉";
            String encrypted = service.encrypt(plaintext);
            
            String decrypted = service.decrypt(encrypted);
            
            assertThat(decrypted).isEqualTo(plaintext);
        }

        @Test
        @DisplayName("should handle emojis")
        void shouldHandleEmojis() throws GeneralSecurityException {
            String plaintext = "🔑🔒🔓💻🖥️";
            String encrypted = service.encrypt(plaintext);
            
            String decrypted = service.decrypt(encrypted);
            
            assertThat(decrypted).isEqualTo(plaintext);
        }
    }
}
