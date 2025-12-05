package org.rostilos.codecrow.webserver.service.auth;

import org.rostilos.codecrow.core.model.user.PasswordResetToken;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.user.twofactor.ETwoFactorType;
import org.rostilos.codecrow.core.model.user.twofactor.TwoFactorAuth;
import org.rostilos.codecrow.core.persistence.repository.user.PasswordResetTokenRepository;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.email.service.EmailService;
import org.rostilos.codecrow.webserver.dto.response.auth.ResetTokenValidationResponse;
import org.rostilos.codecrow.webserver.exception.InvalidResetTokenException;
import org.rostilos.codecrow.webserver.exception.TwoFactorInvalidException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.util.Base64;
import java.util.Optional;

@Service
public class PasswordResetService {
    
    private static final Logger logger = LoggerFactory.getLogger(PasswordResetService.class);
    private static final int TOKEN_LENGTH = 32;
    
    private final UserRepository userRepository;
    private final PasswordResetTokenRepository tokenRepository;
    private final TwoFactorAuthService twoFactorAuthService;
    private final EmailService emailService;
    private final PasswordEncoder passwordEncoder;
    private final SecureRandom secureRandom;
    
    @Value("${codecrow.frontend.url:http://localhost:5173}")
    private String frontendUrl;
    
    public PasswordResetService(
            UserRepository userRepository,
            PasswordResetTokenRepository tokenRepository,
            TwoFactorAuthService twoFactorAuthService,
            EmailService emailService,
            PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.tokenRepository = tokenRepository;
        this.twoFactorAuthService = twoFactorAuthService;
        this.emailService = emailService;
        this.passwordEncoder = passwordEncoder;
        this.secureRandom = new SecureRandom();
    }
    
    /**
     * Request password reset - generates token and sends email
     */
    @Transactional
    public void requestPasswordReset(String email) {
        logger.info("Password reset requested for email: {}", email);
        
        Optional<User> userOpt = userRepository.findByEmail(email);
        
        if (userOpt.isEmpty()) {
            // Don't reveal whether email exists - just log and return silently
            logger.warn("Password reset requested for non-existent email: {}", email);
            return;
        }
        
        User user = userOpt.get();
        
        // Check if user has a password (not OAuth-only account)
        if (user.getPassword() == null || user.getPassword().isEmpty()) {
            logger.warn("Password reset requested for OAuth-only account: {}", email);
            return;
        }
        
        // Invalidate any existing tokens for this user
        tokenRepository.invalidateAllTokensForUser(user);
        
        // Generate new token
        String token = generateSecureToken();
        PasswordResetToken resetToken = new PasswordResetToken(token, user);
        tokenRepository.save(resetToken);
        
        // Send reset email
        sendPasswordResetEmail(user, token);
        
        logger.info("Password reset token generated and email sent for user: {}", user.getId());
    }
    
    /**
     * Validate reset token and return user info including 2FA requirements
     */
    @Transactional(readOnly = true)
    public ResetTokenValidationResponse validateResetToken(String token) {
        Optional<PasswordResetToken> tokenOpt = tokenRepository.findByTokenAndUsedFalse(token);
        
        if (tokenOpt.isEmpty()) {
            return ResetTokenValidationResponse.invalid("Invalid or expired reset token");
        }
        
        PasswordResetToken resetToken = tokenOpt.get();
        
        if (resetToken.isExpired()) {
            return ResetTokenValidationResponse.invalid("Reset token has expired");
        }
        
        User user = resetToken.getUser();
        String maskedEmail = maskEmail(user.getEmail());
        
        // Check if 2FA is enabled
        Optional<TwoFactorAuth> twoFactorOpt = twoFactorAuthService.getTwoFactorStatus(user.getId());
        boolean twoFactorRequired = twoFactorOpt.isPresent() && twoFactorOpt.get().isEnabled();
        String twoFactorType = twoFactorRequired ? twoFactorOpt.get().getTwoFactorType().name() : null;
        
        // If 2FA is EMAIL type, send the code now
        if (twoFactorRequired && ETwoFactorType.EMAIL.name().equals(twoFactorType)) {
            twoFactorAuthService.sendLoginEmailCode(user.getId());
        }
        
        return ResetTokenValidationResponse.valid(maskedEmail, twoFactorRequired, twoFactorType);
    }
    
    /**
     * Reset password with token and optional 2FA code
     */
    @Transactional
    public void resetPassword(String token, String newPassword, String twoFactorCode) {
        logger.info("Password reset attempt with token");
        
        Optional<PasswordResetToken> tokenOpt = tokenRepository.findByTokenAndUsedFalse(token);
        
        if (tokenOpt.isEmpty()) {
            throw new InvalidResetTokenException("Invalid or expired reset token");
        }
        
        PasswordResetToken resetToken = tokenOpt.get();
        
        if (resetToken.isExpired()) {
            throw new InvalidResetTokenException("Reset token has expired");
        }
        
        User user = resetToken.getUser();
        
        // Verify 2FA if enabled
        Optional<TwoFactorAuth> twoFactorOpt = twoFactorAuthService.getTwoFactorStatus(user.getId());
        if (twoFactorOpt.isPresent() && twoFactorOpt.get().isEnabled()) {
            if (twoFactorCode == null || twoFactorCode.isEmpty()) {
                throw new TwoFactorInvalidException("Two-factor authentication code is required");
            }
            
            boolean valid = twoFactorAuthService.verifyLoginCode(user.getId(), twoFactorCode);
            if (!valid) {
                throw new TwoFactorInvalidException("Invalid two-factor authentication code");
            }
        }
        
        // Update password
        user.setPassword(passwordEncoder.encode(newPassword));
        userRepository.save(user);
        
        // Mark token as used
        resetToken.setUsed(true);
        tokenRepository.save(resetToken);
        
        // Invalidate all other tokens for this user
        tokenRepository.invalidateAllTokensForUser(user);
        
        logger.info("Password successfully reset for user: {}", user.getId());
        
        // Send confirmation email
        sendPasswordChangedEmail(user);
    }
    
    /**
     * Generate a secure random token
     */
    private String generateSecureToken() {
        byte[] bytes = new byte[TOKEN_LENGTH];
        secureRandom.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
    
    /**
     * Mask email for privacy (show first 2 chars and domain)
     */
    private String maskEmail(String email) {
        if (email == null || !email.contains("@")) {
            return "***@***.***";
        }
        
        int atIndex = email.indexOf("@");
        String localPart = email.substring(0, atIndex);
        String domain = email.substring(atIndex);
        
        if (localPart.length() <= 2) {
            return "**" + domain;
        }
        
        return localPart.substring(0, 2) + "***" + domain;
    }
    
    /**
     * Send password reset email
     */
    private void sendPasswordResetEmail(User user, String token) {
        String resetUrl = frontendUrl + "/reset-password?token=" + token;
        emailService.sendPasswordResetEmail(user.getEmail(), user.getUsername(), resetUrl);
    }
    
    /**
     * Send password changed confirmation email
     */
    private void sendPasswordChangedEmail(User user) {
        emailService.sendPasswordChangedEmail(user.getEmail(), user.getUsername());
    }
}
