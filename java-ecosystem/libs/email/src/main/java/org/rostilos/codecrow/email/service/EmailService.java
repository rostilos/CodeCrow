package org.rostilos.codecrow.email.service;

public interface EmailService {

    void sendSimpleEmail(String to, String subject, String text);

    void sendHtmlEmail(String to, String subject, String htmlContent);

    void sendTwoFactorCode(String to, String code, int expirationMinutes);

    void sendTwoFactorEnabledNotification(String to, String twoFactorType);

    void sendTwoFactorDisabledNotification(String to);

    void sendBackupCodes(String to, String[] backupCodes);
    
    void sendPasswordResetEmail(String to, String username, String resetUrl);
    
    void sendPasswordChangedEmail(String to, String username);
}
