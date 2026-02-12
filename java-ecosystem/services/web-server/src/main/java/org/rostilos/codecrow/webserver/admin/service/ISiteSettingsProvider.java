package org.rostilos.codecrow.webserver.admin.service;

import org.rostilos.codecrow.core.dto.admin.*;
import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;

import java.util.Map;

/**
 * Abstraction for reading and writing site-wide admin settings.
 * <p>
 * <b>Community / self-hosted</b>: {@code SiteSettingsService} reads/writes the
 * {@code site_settings} database table. Secrets are encrypted at rest.
 * <p>
 * <b>Cloud / SaaS</b>: {@code CloudSiteSettingsService} ({@code @Primary}) reads
 * from {@code @Value} properties and rejects writes.
 */
public interface ISiteSettingsProvider {

    // ─────────────── Typed Getters ───────────────

    BitbucketSettingsDTO getBitbucketSettings();

    GitHubSettingsDTO getGitHubSettings();

    GitLabSettingsDTO getGitLabSettings();

    LlmSyncSettingsDTO getLlmSyncSettings();

    EmbeddingSettingsDTO getEmbeddingSettings();

    SmtpSettingsDTO getSmtpSettings();

    GoogleOAuthSettingsDTO getGoogleOAuthSettings();

    BaseUrlSettingsDTO getBaseUrlSettings();

    // ─────────────── Generic Read / Write ───────────────

    /**
     * Return the raw (decrypted) key-value map for a given settings group.
     * Secret values are masked in the response (e.g. "sk-or-v1-****1234").
     */
    Map<String, String> getSettingsGroupMasked(ESiteSettingsGroup group);

    /**
     * Persist all key-value pairs for the given group, replacing existing values.
     * Secret keys are encrypted before storage.
     *
     * @throws UnsupportedOperationException if the implementation is read-only (cloud).
     */
    void updateSettingsGroup(ESiteSettingsGroup group, Map<String, String> values);

    // ─────────────── Helpers ───────────────

    /**
     * Read a single setting value (decrypted). Returns {@code null} if not set.
     */
    String getValue(ESiteSettingsGroup group, String key);

    /**
     * Returns {@code true} when at least one setting exists for this group.
     */
    boolean isGroupConfigured(ESiteSettingsGroup group);

    /**
     * Returns the configuration status across all groups.
     * Used by the setup wizard to show progress.
     */
    ConfigurationStatusDTO getConfigurationStatus();
}
