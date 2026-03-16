package org.rostilos.codecrow.email;

import com.icegreen.greenmail.configuration.GreenMailConfiguration;
import com.icegreen.greenmail.junit5.GreenMailExtension;
import com.icegreen.greenmail.util.ServerSetup;
import jakarta.mail.Multipart;
import jakarta.mail.internet.MimeMessage;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSenderImpl;
import org.springframework.mail.javamail.MimeMessageHelper;

import java.util.Properties;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration test for email delivery using GreenMail embedded SMTP server.
 * Verifies actual SMTP delivery, HTML rendering, multi-part messages.
 *
 * Uses {@link GreenMailExtension} with a dynamic port to avoid port conflicts
 * in CI environments and ensure proper server lifecycle management.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class EmailDeliveryIT {

    @RegisterExtension
    static GreenMailExtension greenMail = new GreenMailExtension(
            new ServerSetup(0, null, ServerSetup.PROTOCOL_SMTP))
            .withConfiguration(GreenMailConfiguration.aConfig().withDisabledAuthentication());

    private JavaMailSenderImpl mailSender;

    @BeforeEach
    void setup() {
        greenMail.reset();

        mailSender = new JavaMailSenderImpl();
        mailSender.setHost("localhost");
        mailSender.setPort(greenMail.getSmtp().getPort());
        mailSender.setProtocol("smtp");

        Properties props = mailSender.getJavaMailProperties();
        props.put("mail.smtp.auth", "false");
        props.put("mail.smtp.starttls.enable", "false");
    }

    /**
     * Extracts text content from a MimeMessage, handling both plain text
     * and nested multipart (HTML) messages.
     */
    private String extractTextContent(MimeMessage msg) throws Exception {
        Object content = msg.getContent();
        if (content instanceof String s) {
            return s;
        }
        if (content instanceof Multipart multipart) {
            return extractFromMultipart(multipart);
        }
        return content.toString();
    }

    private String extractFromMultipart(Multipart multipart) throws Exception {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < multipart.getCount(); i++) {
            Object partContent = multipart.getBodyPart(i).getContent();
            if (partContent instanceof String s) {
                sb.append(s);
            } else if (partContent instanceof Multipart nested) {
                sb.append(extractFromMultipart(nested));
            }
        }
        return sb.toString();
    }

    @Test
    @Order(1)
    @DisplayName("Should send simple text email")
    void shouldSendSimpleEmail() throws Exception {
        SimpleMailMessage message = new SimpleMailMessage();
        message.setTo("user@test.dev");
        message.setFrom("noreply@codecrow.dev");
        message.setSubject("Welcome to CodeCrow");
        message.setText("Your account has been created.");

        mailSender.send(message);

        MimeMessage[] received = greenMail.getReceivedMessages();
        assertThat(received).hasSize(1);
        assertThat(received[0].getSubject()).isEqualTo("Welcome to CodeCrow");
        assertThat(received[0].getContent().toString().trim()).isEqualTo("Your account has been created.");
    }

    @Test
    @Order(2)
    @DisplayName("Should send HTML email")
    void shouldSendHtmlEmail() throws Exception {
        MimeMessage mimeMessage = mailSender.createMimeMessage();
        MimeMessageHelper helper = new MimeMessageHelper(mimeMessage, true, "UTF-8");
        helper.setTo("user@test.dev");
        helper.setFrom("noreply@codecrow.dev");
        helper.setSubject("Analysis Report");
        helper.setText("<h1>CodeCrow Report</h1><p>5 issues found</p>", true);

        mailSender.send(mimeMessage);

        MimeMessage[] received = greenMail.getReceivedMessages();
        assertThat(received).hasSize(1);
        String content = extractTextContent(received[0]);
        assertThat(content).contains("CodeCrow Report");
        assertThat(content).contains("5 issues found");
    }

    @Test
    @Order(3)
    @DisplayName("Should send 2FA code email")
    void shouldSend2FAEmail() throws Exception {
        SimpleMailMessage message = new SimpleMailMessage();
        message.setTo("user@test.dev");
        message.setFrom("noreply@codecrow.dev");
        message.setSubject("Your 2FA Code");
        message.setText("Your verification code is: 123456. It expires in 5 minutes.");

        mailSender.send(message);

        MimeMessage[] received = greenMail.getReceivedMessages();
        assertThat(received).hasSize(1);
        assertThat(received[0].getSubject()).isEqualTo("Your 2FA Code");
        assertThat(received[0].getContent().toString()).contains("123456");
    }

    @Test
    @Order(4)
    @DisplayName("Should send to multiple recipients")
    void shouldSendToMultipleRecipients() throws Exception {
        SimpleMailMessage message = new SimpleMailMessage();
        message.setTo("user1@test.dev", "user2@test.dev");
        message.setFrom("noreply@codecrow.dev");
        message.setSubject("Team Notification");
        message.setText("New workspace created.");

        mailSender.send(message);

        MimeMessage[] received = greenMail.getReceivedMessages();
        assertThat(received).hasSize(2);
    }

    @Test
    @Order(5)
    @DisplayName("Should handle email with special characters")
    void shouldHandleSpecialCharacters() throws Exception {
        MimeMessage mimeMessage = mailSender.createMimeMessage();
        MimeMessageHelper helper = new MimeMessageHelper(mimeMessage, true, "UTF-8");
        helper.setTo("user@test.dev");
        helper.setFrom("noreply@codecrow.dev");
        helper.setSubject("Análisis Completo — ✅");
        helper.setText("<p>Código analizado: función → resultado</p>", true);

        mailSender.send(mimeMessage);

        MimeMessage[] received = greenMail.getReceivedMessages();
        assertThat(received).hasSize(1);
        assertThat(received[0].getSubject()).contains("Análisis");
    }

    @Test
    @Order(6)
    @DisplayName("Should send backup codes email with formatted list")
    void shouldSendBackupCodesEmail() throws Exception {
        MimeMessage mimeMessage = mailSender.createMimeMessage();
        MimeMessageHelper helper = new MimeMessageHelper(mimeMessage, true, "UTF-8");
        helper.setTo("user@test.dev");
        helper.setFrom("noreply@codecrow.dev");
        helper.setSubject("Your Backup Codes");
        helper.setText("<h2>Backup Codes</h2><ul>" +
                "<li>CODE-0001</li><li>CODE-0002</li><li>CODE-0003</li>" +
                "<li>CODE-0004</li><li>CODE-0005</li></ul>" +
                "<p>Store these securely.</p>", true);

        mailSender.send(mimeMessage);

        MimeMessage[] received = greenMail.getReceivedMessages();
        assertThat(received).hasSize(1);
        String content = extractTextContent(received[0]);
        assertThat(content).contains("CODE-0001", "CODE-0005");
    }
}
