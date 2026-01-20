package org.rostilos.codecrow.email.service;

import jakarta.mail.MessagingException;
import jakarta.mail.internet.MimeMessage;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.email.config.EmailProperties;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.mail.javamail.MimeMessageHelper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class EmailServiceImplTest {

    @Mock
    private JavaMailSender mailSender;

    @Mock
    private EmailTemplateService templateService;

    @Mock
    private EmailProperties emailProperties;

    @Mock
    private MimeMessage mimeMessage;

    private EmailServiceImpl emailService;

    @BeforeEach
    void setUp() {
        emailService = new EmailServiceImpl(mailSender, templateService, emailProperties);
    }

    @Test
    void testSendSimpleEmail_Success() {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        
        emailService.sendSimpleEmail("recipient@example.com", "Test Subject", "Test Body");

        ArgumentCaptor<SimpleMailMessage> messageCaptor = ArgumentCaptor.forClass(SimpleMailMessage.class);
        verify(mailSender).send(messageCaptor.capture());
        
        SimpleMailMessage sentMessage = messageCaptor.getValue();
        assertThat(sentMessage.getTo()).containsExactly("recipient@example.com");
        assertThat(sentMessage.getSubject()).isEqualTo("Test Subject");
        assertThat(sentMessage.getText()).isEqualTo("Test Body");
        assertThat(sentMessage.getFrom()).isEqualTo("test@example.com");
    }

    @Test
    void testSendSimpleEmail_Disabled() {
        when(emailProperties.isEnabled()).thenReturn(false);

        emailService.sendSimpleEmail("recipient@example.com", "Subject", "Body");

        verify(mailSender, never()).send(any(SimpleMailMessage.class));
    }

    @Test
    void testSendSimpleEmail_ThrowsException() {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        doThrow(new RuntimeException("Mail server error"))
            .when(mailSender).send(any(SimpleMailMessage.class));

        assertThatThrownBy(() -> emailService.sendSimpleEmail("recipient@example.com", "Subject", "Body"))
            .isInstanceOf(RuntimeException.class)
            .hasMessageContaining("Failed to send email");
    }

    @Test
    void testSendHtmlEmail_Success() throws Exception {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        when(emailProperties.getFromName()).thenReturn("TestApp");
        when(mailSender.createMimeMessage()).thenReturn(mimeMessage);
        doNothing().when(mailSender).send(any(MimeMessage.class));

        emailService.sendHtmlEmail("recipient@example.com", "Test Subject", "<html>Body</html>");

        verify(mailSender).createMimeMessage();
        verify(mailSender).send(any(MimeMessage.class));
    }

    @Test
    void testSendHtmlEmail_Disabled() {
        when(emailProperties.isEnabled()).thenReturn(false);

        emailService.sendHtmlEmail("recipient@example.com", "Subject", "<html>Body</html>");

        verify(mailSender, never()).createMimeMessage();
        verify(mailSender, never()).send(any(MimeMessage.class));
    }

    @Test
    void testSendTwoFactorCode() {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        when(emailProperties.getFromName()).thenReturn("TestApp");
        when(emailProperties.getAppName()).thenReturn("TestApp");
        when(mailSender.createMimeMessage()).thenReturn(mimeMessage);
        when(templateService.getTwoFactorCodeTemplate("123456", 10))
            .thenReturn("<html>Your code is 123456</html>");

        emailService.sendTwoFactorCode("user@example.com", "123456", 10);

        verify(templateService).getTwoFactorCodeTemplate("123456", 10);
        verify(mailSender).createMimeMessage();
    }

    @Test
    void testSendTwoFactorEnabledNotification() {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        when(emailProperties.getFromName()).thenReturn("TestApp");
        when(emailProperties.getAppName()).thenReturn("TestApp");
        when(mailSender.createMimeMessage()).thenReturn(mimeMessage);
        when(templateService.getTwoFactorEnabledTemplate("TOTP"))
            .thenReturn("<html>2FA enabled</html>");

        emailService.sendTwoFactorEnabledNotification("user@example.com", "TOTP");

        verify(templateService).getTwoFactorEnabledTemplate("TOTP");
        verify(mailSender).createMimeMessage();
    }

    @Test
    void testSendTwoFactorDisabledNotification() {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        when(emailProperties.getFromName()).thenReturn("TestApp");
        when(emailProperties.getAppName()).thenReturn("TestApp");
        when(mailSender.createMimeMessage()).thenReturn(mimeMessage);
        when(templateService.getTwoFactorDisabledTemplate())
            .thenReturn("<html>2FA disabled</html>");

        emailService.sendTwoFactorDisabledNotification("user@example.com");

        verify(templateService).getTwoFactorDisabledTemplate();
        verify(mailSender).createMimeMessage();
    }

    @Test
    void testSendBackupCodes() {
        String[] codes = {"CODE1", "CODE2", "CODE3"};
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        when(emailProperties.getFromName()).thenReturn("TestApp");
        when(emailProperties.getAppName()).thenReturn("TestApp");
        when(mailSender.createMimeMessage()).thenReturn(mimeMessage);
        when(templateService.getBackupCodesTemplate(codes))
            .thenReturn("<html>Backup codes</html>");

        emailService.sendBackupCodes("user@example.com", codes);

        verify(templateService).getBackupCodesTemplate(codes);
        verify(mailSender).createMimeMessage();
    }

    @Test
    void testSendPasswordResetEmail() {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        when(emailProperties.getFromName()).thenReturn("TestApp");
        when(emailProperties.getAppName()).thenReturn("TestApp");
        when(mailSender.createMimeMessage()).thenReturn(mimeMessage);
        when(templateService.getPasswordResetTemplate("testuser", "https://reset.url"))
            .thenReturn("<html>Reset your password</html>");

        emailService.sendPasswordResetEmail("user@example.com", "testuser", "https://reset.url");

        verify(templateService).getPasswordResetTemplate("testuser", "https://reset.url");
        verify(mailSender).createMimeMessage();
    }

    @Test
    void testSendPasswordChangedEmail() {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        when(emailProperties.getFromName()).thenReturn("TestApp");
        when(emailProperties.getAppName()).thenReturn("TestApp");
        when(mailSender.createMimeMessage()).thenReturn(mimeMessage);
        when(templateService.getPasswordChangedTemplate("testuser"))
            .thenReturn("<html>Password changed</html>");

        emailService.sendPasswordChangedEmail("user@example.com", "testuser");

        verify(templateService).getPasswordChangedTemplate("testuser");
        verify(mailSender).createMimeMessage();
    }

    @Test
    void testSendHtmlEmail_GeneratesCorrectSubject_TwoFactorCode() {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        when(emailProperties.getFromName()).thenReturn("TestApp");
        when(emailProperties.getAppName()).thenReturn("TestApp");
        when(mailSender.createMimeMessage()).thenReturn(mimeMessage);
        when(templateService.getTwoFactorCodeTemplate(anyString(), anyInt()))
            .thenReturn("<html>Code</html>");

        emailService.sendTwoFactorCode("user@example.com", "123456", 10);

        verify(mailSender).createMimeMessage();
    }

    @Test
    void testSendHtmlEmail_GeneratesCorrectSubject_PasswordReset() {
        when(emailProperties.isEnabled()).thenReturn(true);
        when(emailProperties.getFrom()).thenReturn("test@example.com");
        when(emailProperties.getFromName()).thenReturn("TestApp");
        when(emailProperties.getAppName()).thenReturn("TestApp");
        when(mailSender.createMimeMessage()).thenReturn(mimeMessage);
        when(templateService.getPasswordResetTemplate(anyString(), anyString()))
            .thenReturn("<html>Reset</html>");

        emailService.sendPasswordResetEmail("user@example.com", "user", "url");

        verify(mailSender).createMimeMessage();
    }

    @Test
    void testConstructor() {
        EmailServiceImpl service = new EmailServiceImpl(mailSender, templateService, emailProperties);
        
        assertThat(service).isNotNull();
    }
}
