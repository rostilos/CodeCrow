package org.rostilos.codecrow.webserver.admin.config;

import org.rostilos.codecrow.core.dto.admin.SmtpSettingsDTO;
import org.rostilos.codecrow.webserver.admin.service.ISiteSettingsProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Primary;
import org.springframework.mail.MailException;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.mail.javamail.JavaMailSenderImpl;
import org.springframework.stereotype.Component;

import jakarta.mail.internet.MimeMessage;
import java.io.InputStream;
import java.util.Properties;

/**
 * Dynamic JavaMailSender that reads SMTP configuration from site settings (DB).
 * <p>
 * In community/self-hosted mode, SMTP settings are managed via the Site Admin panel
 * and stored in the database. This component wraps JavaMailSenderImpl and re-creates
 * the underlying sender whenever configuration changes.
 * <p>
 * In cloud mode, the CloudSiteSettingsService returns values from application.properties,
 * so this effectively behaves like the standard Spring Boot auto-configured sender.
 */
@Primary
@Component("dynamicMailSender")
public class DynamicMailSenderConfig implements JavaMailSender {

    private static final Logger log = LoggerFactory.getLogger(DynamicMailSenderConfig.class);

    private final ISiteSettingsProvider siteSettingsProvider;
    private volatile JavaMailSenderImpl delegate;
    private volatile String lastConfigHash = "";

    public DynamicMailSenderConfig(ISiteSettingsProvider siteSettingsProvider) {
        this.siteSettingsProvider = siteSettingsProvider;
    }

    private JavaMailSenderImpl getDelegate() {
        SmtpSettingsDTO smtp = siteSettingsProvider.getSmtpSettings();
        String configHash = buildConfigHash(smtp);
        
        if (delegate == null || !configHash.equals(lastConfigHash)) {
            synchronized (this) {
                if (delegate == null || !configHash.equals(lastConfigHash)) {
                    delegate = createSender(smtp);
                    lastConfigHash = configHash;
                    log.info("SMTP mail sender (re)configured: host={}, port={}", smtp.host(), smtp.port());
                }
            }
        }
        return delegate;
    }

    private JavaMailSenderImpl createSender(SmtpSettingsDTO smtp) {
        JavaMailSenderImpl sender = new JavaMailSenderImpl();
        sender.setHost(smtp.host() != null ? smtp.host() : "localhost");
        sender.setPort(smtp.port() > 0 ? smtp.port() : 587);
        if (smtp.username() != null && !smtp.username().isBlank()) {
            sender.setUsername(smtp.username());
        }
        if (smtp.password() != null && !smtp.password().isBlank()) {
            sender.setPassword(smtp.password());
        }

        Properties props = sender.getJavaMailProperties();
        props.put("mail.transport.protocol", "smtp");
        if (smtp.starttls()) {
            props.put("mail.smtp.starttls.enable", "true");
        }
        props.put("mail.smtp.auth", String.valueOf(
                smtp.username() != null && !smtp.username().isBlank()));

        return sender;
    }

    private String buildConfigHash(SmtpSettingsDTO smtp) {
        return smtp.host() + ":" + smtp.port() + ":" + smtp.username() + ":" + smtp.starttls();
    }

    // --- JavaMailSender delegation ---

    @Override
    public MimeMessage createMimeMessage() {
        return getDelegate().createMimeMessage();
    }

    @Override
    public MimeMessage createMimeMessage(InputStream contentStream) throws MailException {
        return getDelegate().createMimeMessage(contentStream);
    }

    @Override
    public void send(MimeMessage mimeMessage) throws MailException {
        getDelegate().send(mimeMessage);
    }

    @Override
    public void send(MimeMessage... mimeMessages) throws MailException {
        getDelegate().send(mimeMessages);
    }

    @Override
    public void send(SimpleMailMessage simpleMessage) throws MailException {
        getDelegate().send(simpleMessage);
    }

    @Override
    public void send(SimpleMailMessage... simpleMessages) throws MailException {
        getDelegate().send(simpleMessages);
    }
}
