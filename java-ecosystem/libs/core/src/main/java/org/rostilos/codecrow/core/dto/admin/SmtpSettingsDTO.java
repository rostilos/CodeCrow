package org.rostilos.codecrow.core.dto.admin;

/**
 * SMTP / email configuration.
 * Spring Boot auto-configures JavaMailSender from spring.mail.* properties,
 * but in community/self-hosted mode we write them to DB and override at runtime.
 */
public record SmtpSettingsDTO(
        boolean enabled,
        String host,
        int port,
        String username,
        String password,
        String fromAddress,
        String fromName,
        boolean starttls
) {
    public static final String KEY_ENABLED = "enabled";
    public static final String KEY_HOST = "host";
    public static final String KEY_PORT = "port";
    public static final String KEY_USERNAME = "username";
    public static final String KEY_PASSWORD = "password";
    public static final String KEY_FROM_ADDRESS = "from-address";
    public static final String KEY_FROM_NAME = "from-name";
    public static final String KEY_STARTTLS = "starttls";
}
