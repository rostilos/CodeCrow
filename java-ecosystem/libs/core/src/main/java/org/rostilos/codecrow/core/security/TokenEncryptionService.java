package org.rostilos.codecrow.core.security;

import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.security.GeneralSecurityException;
import java.util.Base64;
import java.security.SecureRandom;

/**
 * AES-GCM encryption service for secrets stored in the database.
 * Supports key rotation via a current + old key pair.
 */
public class TokenEncryptionService {

    private static final String ENCRYPTION_ALGO = "AES/GCM/NoPadding";
    private static final int GCM_IV_LENGTH = 12;
    private static final int GCM_TAG_LENGTH = 128;

    private final SecretKey currentSecretKey;
    private final SecretKey oldSecretKey;

    public TokenEncryptionService(String base64CurrentKey, String base64OldKey) {
        this.currentSecretKey = decodeKey(base64CurrentKey);
        if (base64OldKey != null && !base64OldKey.isEmpty()) {
            this.oldSecretKey = decodeKey(base64OldKey);
        } else {
            this.oldSecretKey = null;
        }
    }

    private SecretKey decodeKey(String base64Key) {
        byte[] keyBytes = Base64.getDecoder().decode(base64Key);
        return new SecretKeySpec(keyBytes, "AES");
    }

    public String encrypt(String data) throws GeneralSecurityException {
        return encryptWithKey(data, currentSecretKey);
    }

    public String decrypt(String encrypted) throws GeneralSecurityException {
        try {
            return decryptWithKey(encrypted, currentSecretKey);
        } catch (GeneralSecurityException e) {
            if (oldSecretKey != null) {
                return decryptWithKey(encrypted, oldSecretKey);
            }
            throw e;
        }
    }

    private String encryptWithKey(String data, SecretKey key) throws GeneralSecurityException {
        Cipher cipher = Cipher.getInstance(ENCRYPTION_ALGO);
        byte[] iv = new byte[GCM_IV_LENGTH];
        new SecureRandom().nextBytes(iv);
        GCMParameterSpec parameterSpec = new GCMParameterSpec(GCM_TAG_LENGTH, iv);
        cipher.init(Cipher.ENCRYPT_MODE, key, parameterSpec);
        byte[] encrypted = cipher.doFinal(data.getBytes());
        byte[] encryptedWithIv = new byte[iv.length + encrypted.length];
        System.arraycopy(iv, 0, encryptedWithIv, 0, iv.length);
        System.arraycopy(encrypted, 0, encryptedWithIv, iv.length, encrypted.length);
        return Base64.getEncoder().encodeToString(encryptedWithIv);
    }

    private String decryptWithKey(String encrypted, SecretKey key) throws GeneralSecurityException {
        byte[] decoded = Base64.getDecoder().decode(encrypted);
        byte[] iv = new byte[GCM_IV_LENGTH];
        byte[] encryptedBytes = new byte[decoded.length - GCM_IV_LENGTH];
        System.arraycopy(decoded, 0, iv, 0, GCM_IV_LENGTH);
        System.arraycopy(decoded, GCM_IV_LENGTH, encryptedBytes, 0, encryptedBytes.length);
        Cipher cipher = Cipher.getInstance(ENCRYPTION_ALGO);
        GCMParameterSpec parameterSpec = new GCMParameterSpec(GCM_TAG_LENGTH, iv);
        cipher.init(Cipher.DECRYPT_MODE, key, parameterSpec);
        byte[] original = cipher.doFinal(encryptedBytes);
        return new String(original);
    }
}
