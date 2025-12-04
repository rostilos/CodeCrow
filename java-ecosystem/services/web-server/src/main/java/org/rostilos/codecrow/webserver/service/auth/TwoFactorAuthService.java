package org.rostilos.codecrow.webserver.service.auth;

import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.user.twofactor.ETwoFactorType;
import org.rostilos.codecrow.core.model.user.twofactor.TwoFactorAuth;
import org.rostilos.codecrow.core.persistence.repository.user.TwoFactorAuthRepository;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.email.service.EmailService;
import org.rostilos.codecrow.webserver.dto.response.auth.TwoFactorSetupResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.InvalidKeyException;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.Optional;

@Service
public class TwoFactorAuthService {
    
    private static final Logger logger = LoggerFactory.getLogger(TwoFactorAuthService.class);
    private static final String HMAC_ALGORITHM = "HmacSHA1";
    private static final int TOTP_DIGITS = 6;
    private static final int TOTP_PERIOD = 30; // seconds
    private static final int TOTP_WINDOW = 1; // Allow 1 period before/after for clock drift
    private static final int EMAIL_CODE_LENGTH = 6;
    private static final int EMAIL_CODE_EXPIRY_MINUTES = 10;
    private static final int BACKUP_CODES_COUNT = 10;
    private static final int BACKUP_CODE_LENGTH = 8;
    private static final String BASE32_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    
    private final TwoFactorAuthRepository twoFactorAuthRepository;
    private final UserRepository userRepository;
    private final EmailService emailService;
    private final SecureRandom secureRandom;
    
    @Value("${codecrow.app.name:CodeCrow}")
    private String appName;
    
    public TwoFactorAuthService(
            TwoFactorAuthRepository twoFactorAuthRepository,
            UserRepository userRepository,
            EmailService emailService) {
        this.twoFactorAuthRepository = twoFactorAuthRepository;
        this.userRepository = userRepository;
        this.emailService = emailService;
        this.secureRandom = new SecureRandom();
    }
    
    /**
     * Initialize 2FA setup for TOTP (Google Authenticator)
     */
    @Transactional
    public TwoFactorSetupResponse initializeTotpSetup(Long userId) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new RuntimeException("User not found"));
        
        Optional<TwoFactorAuth> existing = twoFactorAuthRepository.findByUser(user);
        if (existing.isPresent() && existing.get().isEnabled()) {
            throw new RuntimeException("Two-factor authentication is already enabled");
        }
        
        String secretKey = generateSecretKey();
        
        TwoFactorAuth twoFactorAuth = existing.orElse(new TwoFactorAuth(user, ETwoFactorType.TOTP));
        twoFactorAuth.setTwoFactorType(ETwoFactorType.TOTP);
        twoFactorAuth.setSecretKey(secretKey);
        twoFactorAuth.setVerified(false);
        twoFactorAuth.setEnabled(false);
        
        twoFactorAuthRepository.save(twoFactorAuth);
        
        String qrCodeUrl = generateQRCodeUrl(user.getEmail(), secretKey);
        
        return new TwoFactorSetupResponse(
                secretKey,
                qrCodeUrl,
                ETwoFactorType.TOTP.name(),
                false
        );
    }
    
    /**
     * Initialize 2FA setup for Email
     */
    @Transactional
    public TwoFactorSetupResponse initializeEmailSetup(Long userId) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new RuntimeException("User not found"));
        
        Optional<TwoFactorAuth> existing = twoFactorAuthRepository.findByUser(user);
        if (existing.isPresent() && existing.get().isEnabled()) {
            throw new RuntimeException("Two-factor authentication is already enabled");
        }
        
        TwoFactorAuth twoFactorAuth = existing.orElse(new TwoFactorAuth(user, ETwoFactorType.EMAIL));
        twoFactorAuth.setTwoFactorType(ETwoFactorType.EMAIL);
        twoFactorAuth.setVerified(false);
        twoFactorAuth.setEnabled(false);
        
        String emailCode = generateEmailCode();
        twoFactorAuth.setEmailCode(emailCode);
        twoFactorAuth.setEmailCodeExpiresAt(Instant.now().plus(EMAIL_CODE_EXPIRY_MINUTES, ChronoUnit.MINUTES));
        
        twoFactorAuthRepository.save(twoFactorAuth);
        
        emailService.sendTwoFactorCode(user.getEmail(), emailCode, EMAIL_CODE_EXPIRY_MINUTES);
        
        return new TwoFactorSetupResponse(
                null,
                null,
                ETwoFactorType.EMAIL.name(),
                false
        );
    }
    
    /**
     * Verify and enable 2FA
     */
    @Transactional
    public String[] verifyAndEnable(Long userId, String code) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new RuntimeException("User not found"));
        
        TwoFactorAuth twoFactorAuth = twoFactorAuthRepository.findByUser(user)
                .orElseThrow(() -> new RuntimeException("Two-factor setup not found. Please start setup first."));
        
        boolean isValid;
        if (twoFactorAuth.getTwoFactorType() == ETwoFactorType.TOTP) {
            isValid = verifyTotpCode(twoFactorAuth.getSecretKey(), code);
        } else {
            isValid = verifyEmailCode(twoFactorAuth, code);
        }
        
        if (!isValid) {
            throw new RuntimeException("Invalid verification code");
        }
        
        String[] backupCodes = generateBackupCodes();
        twoFactorAuth.setBackupCodes(String.join(",", backupCodes));
        
        twoFactorAuth.setVerified(true);
        twoFactorAuth.setEnabled(true);
        twoFactorAuth.setEmailCode(null);
        twoFactorAuth.setEmailCodeExpiresAt(null);
        
        twoFactorAuthRepository.save(twoFactorAuth);
        
        emailService.sendTwoFactorEnabledNotification(user.getEmail(), twoFactorAuth.getTwoFactorType().name());
        emailService.sendBackupCodes(user.getEmail(), backupCodes);
        
        logger.info("2FA enabled for user: {}", user.getEmail());
        
        return backupCodes;
    }
    
    /**
     * Verify 2FA code during login
     */
    public boolean verifyLoginCode(Long userId, String code) {
        TwoFactorAuth twoFactorAuth = twoFactorAuthRepository.findByUserId(userId)
                .orElseThrow(() -> new RuntimeException("Two-factor authentication not found"));
        
        if (!twoFactorAuth.isEnabled()) {
            throw new RuntimeException("Two-factor authentication is not enabled");
        }
        
        // First try TOTP or Email code
        if (twoFactorAuth.getTwoFactorType() == ETwoFactorType.TOTP) {
            if (verifyTotpCode(twoFactorAuth.getSecretKey(), code)) {
                return true;
            }
        } else {
            if (verifyEmailCode(twoFactorAuth, code)) {
                return true;
            }
        }
        
        // Try backup codes
        return verifyAndConsumeBackupCode(twoFactorAuth, code);
    }
    
    /**
     * Send email verification code for login
     */
    @Transactional
    public void sendLoginEmailCode(Long userId) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new RuntimeException("User not found"));
        
        TwoFactorAuth twoFactorAuth = twoFactorAuthRepository.findByUser(user)
                .orElseThrow(() -> new RuntimeException("Two-factor authentication not found"));
        
        if (twoFactorAuth.getTwoFactorType() != ETwoFactorType.EMAIL) {
            throw new RuntimeException("Email 2FA is not enabled for this account");
        }
        
        String emailCode = generateEmailCode();
        twoFactorAuth.setEmailCode(emailCode);
        twoFactorAuth.setEmailCodeExpiresAt(Instant.now().plus(EMAIL_CODE_EXPIRY_MINUTES, ChronoUnit.MINUTES));
        
        twoFactorAuthRepository.save(twoFactorAuth);
        emailService.sendTwoFactorCode(user.getEmail(), emailCode, EMAIL_CODE_EXPIRY_MINUTES);
    }
    
    /**
     * Disable 2FA
     */
    @Transactional
    public void disable(Long userId, String code) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new RuntimeException("User not found"));
        
        TwoFactorAuth twoFactorAuth = twoFactorAuthRepository.findByUser(user)
                .orElseThrow(() -> new RuntimeException("Two-factor authentication not found"));
        
        boolean isValid;
        if (twoFactorAuth.getTwoFactorType() == ETwoFactorType.TOTP) {
            isValid = verifyTotpCode(twoFactorAuth.getSecretKey(), code);
        } else {
            isValid = verifyEmailCode(twoFactorAuth, code);
        }
        
        if (!isValid && !verifyAndConsumeBackupCode(twoFactorAuth, code)) {
            throw new RuntimeException("Invalid verification code");
        }
        
        twoFactorAuthRepository.delete(twoFactorAuth);
        emailService.sendTwoFactorDisabledNotification(user.getEmail());
        
        logger.info("2FA disabled for user: {}", user.getEmail());
    }
    
    /**
     * Regenerate backup codes
     */
    @Transactional
    public String[] regenerateBackupCodes(Long userId, String code) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new RuntimeException("User not found"));
        
        TwoFactorAuth twoFactorAuth = twoFactorAuthRepository.findByUser(user)
                .orElseThrow(() -> new RuntimeException("Two-factor authentication not found"));
        
        boolean isValid;
        if (twoFactorAuth.getTwoFactorType() == ETwoFactorType.TOTP) {
            isValid = verifyTotpCode(twoFactorAuth.getSecretKey(), code);
        } else {
            isValid = verifyEmailCode(twoFactorAuth, code);
        }
        
        if (!isValid) {
            throw new RuntimeException("Invalid verification code");
        }
        
        String[] newBackupCodes = generateBackupCodes();
        twoFactorAuth.setBackupCodes(String.join(",", newBackupCodes));
        twoFactorAuthRepository.save(twoFactorAuth);
        
        emailService.sendBackupCodes(user.getEmail(), newBackupCodes);
        
        return newBackupCodes;
    }
    
    /**
     * Get 2FA status for a user
     */
    public Optional<TwoFactorAuth> getTwoFactorStatus(Long userId) {
        return twoFactorAuthRepository.findByUserId(userId);
    }
    
    /**
     * Check if user has 2FA enabled
     */
    public boolean isTwoFactorEnabled(Long userId) {
        return twoFactorAuthRepository.existsByUserIdAndEnabledTrue(userId);
    }

    private String generateSecretKey() {
        byte[] bytes = new byte[20];
        secureRandom.nextBytes(bytes);
        return encodeBase32(bytes);
    }
    
    private String encodeBase32(byte[] data) {
        StringBuilder result = new StringBuilder();
        int buffer = 0;
        int bitsLeft = 0;
        
        for (byte b : data) {
            buffer = (buffer << 8) | (b & 0xFF);
            bitsLeft += 8;
            
            while (bitsLeft >= 5) {
                int index = (buffer >> (bitsLeft - 5)) & 0x1F;
                result.append(BASE32_CHARS.charAt(index));
                bitsLeft -= 5;
            }
        }
        
        if (bitsLeft > 0) {
            int index = (buffer << (5 - bitsLeft)) & 0x1F;
            result.append(BASE32_CHARS.charAt(index));
        }
        
        return result.toString();
    }
    
    private byte[] decodeBase32(String base32) {
        base32 = base32.toUpperCase().replaceAll("[^A-Z2-7]", "");
        
        int byteCount = base32.length() * 5 / 8;
        byte[] result = new byte[byteCount];
        
        int buffer = 0;
        int bitsLeft = 0;
        int index = 0;
        
        for (char c : base32.toCharArray()) {
            int value = BASE32_CHARS.indexOf(c);
            if (value < 0) continue;
            
            buffer = (buffer << 5) | value;
            bitsLeft += 5;
            
            if (bitsLeft >= 8) {
                result[index++] = (byte) (buffer >> (bitsLeft - 8));
                bitsLeft -= 8;
            }
        }
        
        return result;
    }
    
    private String generateEmailCode() {
        StringBuilder code = new StringBuilder();
        for (int i = 0; i < EMAIL_CODE_LENGTH; i++) {
            code.append(secureRandom.nextInt(10));
        }
        return code.toString();
    }
    
    private String[] generateBackupCodes() {
        String[] codes = new String[BACKUP_CODES_COUNT];
        String chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
        
        for (int i = 0; i < BACKUP_CODES_COUNT; i++) {
            StringBuilder code = new StringBuilder();
            for (int j = 0; j < BACKUP_CODE_LENGTH; j++) {
                if (j == 4) code.append("-");
                code.append(chars.charAt(secureRandom.nextInt(chars.length())));
            }
            codes[i] = code.toString();
        }
        return codes;
    }
    
    private String generateQRCodeUrl(String email, String secret) {
        String issuer = URLEncoder.encode(appName, StandardCharsets.UTF_8);
        String account = URLEncoder.encode(email, StandardCharsets.UTF_8);
        
        String otpAuthUrl = String.format(
                "otpauth://totp/%s:%s?secret=%s&issuer=%s&algorithm=SHA1&digits=%d&period=%d",
                issuer, account, secret, issuer, TOTP_DIGITS, TOTP_PERIOD
        );
        
        return String.format(
                "https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=%s",
                URLEncoder.encode(otpAuthUrl, StandardCharsets.UTF_8)
        );
    }
    
    private boolean verifyTotpCode(String secret, String code) {
        if (code == null || code.length() != TOTP_DIGITS) {
            return false;
        }
        
        try {
            long currentTime = System.currentTimeMillis() / 1000;
            long counter = currentTime / TOTP_PERIOD;
            
            for (int i = -TOTP_WINDOW; i <= TOTP_WINDOW; i++) {
                String expectedCode = generateTotpCode(secret, counter + i);
                if (expectedCode.equals(code)) {
                    return true;
                }
            }
        } catch (Exception e) {
            logger.error("Error verifying TOTP code", e);
        }
        
        return false;
    }
    
    private String generateTotpCode(String secret, long counter) 
            throws NoSuchAlgorithmException, InvalidKeyException {
        byte[] key = decodeBase32(secret);
        byte[] data = new byte[8];
        
        for (int i = 7; i >= 0; i--) {
            data[i] = (byte) (counter & 0xff);
            counter >>= 8;
        }
        
        Mac mac = Mac.getInstance(HMAC_ALGORITHM);
        mac.init(new SecretKeySpec(key, HMAC_ALGORITHM));
        byte[] hash = mac.doFinal(data);
        
        int offset = hash[hash.length - 1] & 0xf;
        int binary = ((hash[offset] & 0x7f) << 24)
                | ((hash[offset + 1] & 0xff) << 16)
                | ((hash[offset + 2] & 0xff) << 8)
                | (hash[offset + 3] & 0xff);
        
        int otp = binary % (int) Math.pow(10, TOTP_DIGITS);
        return String.format("%0" + TOTP_DIGITS + "d", otp);
    }
    
    private boolean verifyEmailCode(TwoFactorAuth twoFactorAuth, String code) {
        if (code == null || twoFactorAuth.getEmailCode() == null) {
            return false;
        }
        
        if (twoFactorAuth.isEmailCodeExpired()) {
            return false;
        }
        
        return twoFactorAuth.getEmailCode().equals(code);
    }
    
    @Transactional
    private boolean verifyAndConsumeBackupCode(TwoFactorAuth twoFactorAuth, String code) {
        if (code == null || twoFactorAuth.getBackupCodes() == null) {
            return false;
        }
        
        String[] backupCodes = twoFactorAuth.getBackupCodes().split(",");
        String normalizedCode = code.toUpperCase().replace("-", "");
        
        for (int i = 0; i < backupCodes.length; i++) {
            String storedCode = backupCodes[i].replace("-", "");
            if (storedCode.equals(normalizedCode)) {
                // Remove used backup code
                backupCodes[i] = "USED";
                twoFactorAuth.setBackupCodes(String.join(",", backupCodes));
                twoFactorAuthRepository.save(twoFactorAuth);
                
                logger.info("Backup code used for user: {}", twoFactorAuth.getUser().getEmail());
                return true;
            }
        }
        
        return false;
    }
}
