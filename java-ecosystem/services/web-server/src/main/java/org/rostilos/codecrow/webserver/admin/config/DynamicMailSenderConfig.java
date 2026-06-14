package org.rostilos.codecrow.webserver.admin.config;

import org.rostilos.codecrow.core.dto.admin.SmtpSettingsDTO;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.mail.javamail.JavaMailSenderImpl;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
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
@Configuration
public class DynamicMailSenderConfig {

    private static final Logger log = LoggerFactory.getLogger(DynamicMailSenderConfig.class);

    @Bean("dynamicMailSender")
    @Primary
    public JavaMailSender dynamicMailSender(SiteSettingsProvider siteSettingsProvider) {
        return (JavaMailSender) Proxy.newProxyInstance(
                JavaMailSender.class.getClassLoader(),
                new Class<?>[]{JavaMailSender.class},
                new DynamicMailSenderInvocationHandler(siteSettingsProvider)
        );
    }

    private static final class DynamicMailSenderInvocationHandler implements InvocationHandler {
        private final SiteSettingsProvider siteSettingsProvider;
        private volatile JavaMailSenderImpl delegate;
        private volatile String lastConfigHash = "";

        private DynamicMailSenderInvocationHandler(SiteSettingsProvider siteSettingsProvider) {
            this.siteSettingsProvider = siteSettingsProvider;
        }

        @Override
        public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
            if (method.getDeclaringClass() == Object.class) {
                return switch (method.getName()) {
                    case "toString" -> "Dynamic JavaMailSender proxy";
                    case "hashCode" -> System.identityHashCode(proxy);
                    case "equals" -> proxy == args[0];
                    default -> method.invoke(this, args);
                };
            }

            try {
                return method.invoke(getDelegate(), args);
            } catch (InvocationTargetException e) {
                throw e.getTargetException();
            }
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
            int passwordHash = smtp.password() != null ? smtp.password().hashCode() : 0;
            return smtp.host() + ":" + smtp.port() + ":" + smtp.username() + ":"
                    + smtp.starttls() + ":" + passwordHash;
        }
    }
}
