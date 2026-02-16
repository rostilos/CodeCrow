package org.rostilos.codecrow.core.model.admin;

import jakarta.persistence.*;
import java.time.OffsetDateTime;

/**
 * Stores site-wide configuration key-value pairs, organised by group.
 * Values that contain secrets (API keys, passwords) are stored encrypted
 * via {@link org.rostilos.codecrow.security.oauth.TokenEncryptionService}.
 */
@Entity
@Table(
    name = "site_settings",
    uniqueConstraints = @UniqueConstraint(
        name = "uk_site_settings_group_key",
        columnNames = {"config_group", "config_key"}
    )
)
public class SiteSettings {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Enumerated(EnumType.STRING)
    @Column(name = "config_group", nullable = false, length = 32)
    private ESiteSettingsGroup configGroup;

    @Column(name = "config_key", nullable = false, length = 128)
    private String configKey;

    /**
     * Stored encrypted when the key is a secret (API key, password, etc.),
     * stored as plain text otherwise.
     */
    @Column(name = "config_value", columnDefinition = "TEXT")
    private String configValue;

    @Column(name = "is_secret", nullable = false)
    private boolean secret;

    @Column(name = "created_at", nullable = false, updatable = false)
    private final OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    // ────────────────── Getters / Setters ──────────────────

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public ESiteSettingsGroup getConfigGroup() {
        return configGroup;
    }

    public void setConfigGroup(ESiteSettingsGroup configGroup) {
        this.configGroup = configGroup;
    }

    public String getConfigKey() {
        return configKey;
    }

    public void setConfigKey(String configKey) {
        this.configKey = configKey;
    }

    public String getConfigValue() {
        return configValue;
    }

    public void setConfigValue(String configValue) {
        this.configValue = configValue;
    }

    public boolean isSecret() {
        return secret;
    }

    public void setSecret(boolean secret) {
        this.secret = secret;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }
}
