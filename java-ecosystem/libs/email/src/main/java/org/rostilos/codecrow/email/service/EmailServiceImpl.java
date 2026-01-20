package org.rostilos.codecrow.email.service;

import jakarta.mail.MessagingException;
import jakarta.mail.internet.MimeMessage;
import org.rostilos.codecrow.email.config.EmailProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.mail.javamail.MimeMessageHelper;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

/**
 * Async email service implementation.
 * All email sending is performed asynchronously to avoid blocking the calling thread.
 */
@Service
public class EmailServiceImpl implements EmailService {
    
    private static final Logger logger = LoggerFactory.getLogger(EmailServiceImpl.class);
    
    private final JavaMailSender mailSender;
    private final EmailTemplateService templateService;
    private final EmailProperties emailProperties;
    
    public EmailServiceImpl(JavaMailSender mailSender, 
                            EmailTemplateService templateService,
                            EmailProperties emailProperties) {
        this.mailSender = mailSender;
        this.templateService = templateService;
        this.emailProperties = emailProperties;
    }
    
    @Override
    @Async("emailExecutor")
    public void sendSimpleEmail(String to, String subject, String text) {
        if (!emailProperties.isEnabled()) {
            logger.warn("Email sending is disabled. Would have sent email to: {}", to);
            return;
        }
        
        try {
            SimpleMailMessage message = new SimpleMailMessage();
            message.setFrom(emailProperties.getFrom());
            message.setTo(to);
            message.setSubject(subject);
            message.setText(text);
            mailSender.send(message);
            logger.info("Simple email sent to: {}", to);
        } catch (Exception e) {
            logger.error("Failed to send simple email to: {}", to, e);
            throw new RuntimeException("Failed to send email", e);
        }
    }
    
    @Override
    @Async("emailExecutor")
    public void sendHtmlEmail(String to, String subject, String htmlContent) {
        if (!emailProperties.isEnabled()) {
            logger.warn("Email sending is disabled. Would have sent HTML email to: {}", to);
            return;
        }
        
        try {
            MimeMessage message = mailSender.createMimeMessage();
            MimeMessageHelper helper = new MimeMessageHelper(message, true, "UTF-8");
            
            helper.setFrom(emailProperties.getFrom(), emailProperties.getFromName());
            helper.setTo(to);
            helper.setSubject(subject);
            helper.setText(htmlContent, true);
            
            mailSender.send(message);
            logger.info("HTML email sent to: {}", to);
        } catch (MessagingException e) {
            logger.error("Failed to send HTML email to: {}", to, e);
            throw new RuntimeException("Failed to send email", e);
        } catch (Exception e) {
            logger.error("Failed to send HTML email to: {}", to, e);
            throw new RuntimeException("Failed to send email", e);
        }
    }
    
    @Override
    public void sendTwoFactorCode(String to, String code, int expirationMinutes) {
        String subject = emailProperties.getAppName() + " - Your Verification Code";
        String htmlContent = templateService.getTwoFactorCodeTemplate(code, expirationMinutes);
        sendHtmlEmail(to, subject, htmlContent);
    }
    
    @Override
    public void sendTwoFactorEnabledNotification(String to, String twoFactorType) {
        String subject = emailProperties.getAppName() + " - Two-Factor Authentication Enabled";
        String htmlContent = templateService.getTwoFactorEnabledTemplate(twoFactorType);
        sendHtmlEmail(to, subject, htmlContent);
    }
    
    @Override
    public void sendTwoFactorDisabledNotification(String to) {
        String subject = emailProperties.getAppName() + " - Two-Factor Authentication Disabled";
        String htmlContent = templateService.getTwoFactorDisabledTemplate();
        sendHtmlEmail(to, subject, htmlContent);
    }
    
    @Override
    public void sendBackupCodes(String to, String[] backupCodes) {
        String subject = emailProperties.getAppName() + " - Your Backup Codes";
        String htmlContent = templateService.getBackupCodesTemplate(backupCodes);
        sendHtmlEmail(to, subject, htmlContent);
    }
    
    @Override
    public void sendPasswordResetEmail(String to, String username, String resetUrl) {
        String subject = emailProperties.getAppName() + " - Password Reset Request";
        String htmlContent = templateService.getPasswordResetTemplate(username, resetUrl);
        sendHtmlEmail(to, subject, htmlContent);
    }
    
    @Override
    public void sendPasswordChangedEmail(String to, String username) {
        String subject = emailProperties.getAppName() + " - Password Changed Successfully";
        String htmlContent = templateService.getPasswordChangedTemplate(username);
        sendHtmlEmail(to, subject, htmlContent);
    }
}
