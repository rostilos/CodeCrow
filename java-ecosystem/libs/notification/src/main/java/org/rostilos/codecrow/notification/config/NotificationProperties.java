package org.rostilos.codecrow.notification.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * Configuration properties for the notification module.
 */
@Component
@ConfigurationProperties(prefix = "codecrow.notification")
public class NotificationProperties {

    /**
     * Whether to check for expiring tokens on schedule.
     */
    private boolean tokenExpirationCheckEnabled = true;

    /**
     * Number of days before token expiration to send warning.
     */
    private int tokenExpirationWarningDays = 7;

    /**
     * Days to retain read notifications before cleanup.
     */
    private int readNotificationRetentionDays = 30;

    /**
     * Hours within which duplicate notifications are suppressed.
     */
    private int duplicateSuppressionHours = 24;

    /**
     * Whether to send email notifications by default.
     */
    private boolean emailNotificationsEnabled = true;

    /**
     * Maximum notifications per page in API responses.
     */
    private int maxPageSize = 50;

    /**
     * Default notifications per page.
     */
    private int defaultPageSize = 20;

    // Getters and Setters

    public boolean isTokenExpirationCheckEnabled() {
        return tokenExpirationCheckEnabled;
    }

    public void setTokenExpirationCheckEnabled(boolean tokenExpirationCheckEnabled) {
        this.tokenExpirationCheckEnabled = tokenExpirationCheckEnabled;
    }

    public int getTokenExpirationWarningDays() {
        return tokenExpirationWarningDays;
    }

    public void setTokenExpirationWarningDays(int tokenExpirationWarningDays) {
        this.tokenExpirationWarningDays = tokenExpirationWarningDays;
    }

    public int getReadNotificationRetentionDays() {
        return readNotificationRetentionDays;
    }

    public void setReadNotificationRetentionDays(int readNotificationRetentionDays) {
        this.readNotificationRetentionDays = readNotificationRetentionDays;
    }

    public int getDuplicateSuppressionHours() {
        return duplicateSuppressionHours;
    }

    public void setDuplicateSuppressionHours(int duplicateSuppressionHours) {
        this.duplicateSuppressionHours = duplicateSuppressionHours;
    }

    public boolean isEmailNotificationsEnabled() {
        return emailNotificationsEnabled;
    }

    public void setEmailNotificationsEnabled(boolean emailNotificationsEnabled) {
        this.emailNotificationsEnabled = emailNotificationsEnabled;
    }

    public int getMaxPageSize() {
        return maxPageSize;
    }

    public void setMaxPageSize(int maxPageSize) {
        this.maxPageSize = maxPageSize;
    }

    public int getDefaultPageSize() {
        return defaultPageSize;
    }

    public void setDefaultPageSize(int defaultPageSize) {
        this.defaultPageSize = defaultPageSize;
    }
}
