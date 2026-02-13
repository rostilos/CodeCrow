package org.rostilos.codecrow.webserver.admin.service;

import org.rostilos.codecrow.core.dto.admin.*;
import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;
import org.springframework.core.io.Resource;
import org.springframework.web.multipart.MultipartFile;

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

    /**
     * Securely download the GitHub App private key file.
     * <p>
     * Only allowed for self-hosted instances. The file must match the stored
     * {@code private-key-path} setting, must have a {@code .pem} extension,
     * and must not contain path-traversal sequences.
     *
     * @return the private key file as a Spring Resource
     * @throws UnsupportedOperationException in cloud mode
     * @throws SecurityException if the path fails validation
     * @throws java.io.FileNotFoundException if the file does not exist
     */
    Resource downloadPrivateKeyFile();

    /**
     * Upload a GitHub App private key (.pem) file to a secure directory
     * on the server. Returns the absolute path where the file was saved.
     *
     * @param file the uploaded .pem file
     * @return the absolute path to the saved file
     * @throws UnsupportedOperationException in cloud mode
     * @throws SecurityException if the file fails validation
     * @throws IllegalArgumentException if the file is empty or too large
     */
    String uploadPrivateKeyFile(MultipartFile file);
}
