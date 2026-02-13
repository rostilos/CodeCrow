package org.rostilos.codecrow.webserver.admin.service;

import org.rostilos.codecrow.core.dto.admin.*;
import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;
import org.rostilos.codecrow.core.model.admin.SiteSettings;
import org.rostilos.codecrow.core.persistence.repository.admin.SiteSettingsRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.FileNotFoundException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Community / self-hosted implementation of {@link ISiteSettingsProvider}.
 * Reads and writes the {@code site_settings} database table.
 * Secrets are encrypted at rest via {@link TokenEncryptionService}.
 */
@Service
public class SiteSettingsService implements ISiteSettingsProvider {

    private static final Logger log = LoggerFactory.getLogger(SiteSettingsService.class);

    /**
     * Keys within each group whose values must be encrypted.
     */
    private static final Set<String> SECRET_KEYS = new java.util.HashSet<>(java.util.Arrays.asList(
            BitbucketSettingsDTO.KEY_CLIENT_SECRET,
            LlmSyncSettingsDTO.KEY_OPENROUTER_API_KEY,
            LlmSyncSettingsDTO.KEY_OPENAI_API_KEY,
            LlmSyncSettingsDTO.KEY_ANTHROPIC_API_KEY,
            LlmSyncSettingsDTO.KEY_GOOGLE_API_KEY,
            EmbeddingSettingsDTO.KEY_OPENROUTER_API_KEY,
            SmtpSettingsDTO.KEY_PASSWORD,
            GitLabSettingsDTO.KEY_CLIENT_SECRET
    ));

    /**
     * Minimum number of required groups to consider the setup "complete".
     * At minimum the admin needs at least one VCS provider configured.
     */
    private static final Set<ESiteSettingsGroup> REQUIRED_GROUPS = Set.of(
            ESiteSettingsGroup.BASE_URLS
    );

    private final SiteSettingsRepository repository;
    private final TokenEncryptionService encryptionService;

    public SiteSettingsService(SiteSettingsRepository repository,
                               TokenEncryptionService encryptionService) {
        this.repository = repository;
        this.encryptionService = encryptionService;
    }

    // ─────────────── Typed Getters ───────────────

    @Override
    public BitbucketSettingsDTO getBitbucketSettings() {
        Map<String, String> m = getRawValues(ESiteSettingsGroup.VCS_BITBUCKET);
        return new BitbucketSettingsDTO(
                m.getOrDefault(BitbucketSettingsDTO.KEY_CLIENT_ID, ""),
                m.getOrDefault(BitbucketSettingsDTO.KEY_CLIENT_SECRET, "")
        );
    }

    @Override
    public GitHubSettingsDTO getGitHubSettings() {
        Map<String, String> m = getRawValues(ESiteSettingsGroup.VCS_GITHUB);
        return new GitHubSettingsDTO(
                m.getOrDefault(GitHubSettingsDTO.KEY_APP_ID, ""),
                m.getOrDefault(GitHubSettingsDTO.KEY_PRIVATE_KEY_PATH, "")
        );
    }

    @Override
    public GitLabSettingsDTO getGitLabSettings() {
        Map<String, String> m = getRawValues(ESiteSettingsGroup.VCS_GITLAB);
        return new GitLabSettingsDTO(
                m.getOrDefault(GitLabSettingsDTO.KEY_CLIENT_ID, ""),
                m.getOrDefault(GitLabSettingsDTO.KEY_CLIENT_SECRET, ""),
                m.getOrDefault(GitLabSettingsDTO.KEY_BASE_URL, "https://gitlab.com")
        );
    }

    @Override
    public LlmSyncSettingsDTO getLlmSyncSettings() {
        Map<String, String> m = getRawValues(ESiteSettingsGroup.LLM_SYNC);
        return new LlmSyncSettingsDTO(
                m.getOrDefault(LlmSyncSettingsDTO.KEY_OPENROUTER_API_KEY, ""),
                m.getOrDefault(LlmSyncSettingsDTO.KEY_OPENAI_API_KEY, ""),
                m.getOrDefault(LlmSyncSettingsDTO.KEY_ANTHROPIC_API_KEY, ""),
                m.getOrDefault(LlmSyncSettingsDTO.KEY_GOOGLE_API_KEY, "")
        );
    }

    @Override
    public EmbeddingSettingsDTO getEmbeddingSettings() {
        Map<String, String> m = getRawValues(ESiteSettingsGroup.EMBEDDING);
        return new EmbeddingSettingsDTO(
                m.getOrDefault(EmbeddingSettingsDTO.KEY_PROVIDER, "ollama"),
                m.getOrDefault(EmbeddingSettingsDTO.KEY_OLLAMA_BASE_URL, "http://localhost:11434"),
                m.getOrDefault(EmbeddingSettingsDTO.KEY_OLLAMA_MODEL, "qwen3-embedding:0.6b"),
                m.getOrDefault(EmbeddingSettingsDTO.KEY_OPENROUTER_API_KEY, ""),
                m.getOrDefault(EmbeddingSettingsDTO.KEY_OPENROUTER_MODEL, "qwen/qwen3-embedding-8b")
        );
    }

    @Override
    public SmtpSettingsDTO getSmtpSettings() {
        Map<String, String> m = getRawValues(ESiteSettingsGroup.SMTP);
        return new SmtpSettingsDTO(
                Boolean.parseBoolean(m.getOrDefault(SmtpSettingsDTO.KEY_ENABLED, "false")),
                m.getOrDefault(SmtpSettingsDTO.KEY_HOST, ""),
                Integer.parseInt(m.getOrDefault(SmtpSettingsDTO.KEY_PORT, "587")),
                m.getOrDefault(SmtpSettingsDTO.KEY_USERNAME, ""),
                m.getOrDefault(SmtpSettingsDTO.KEY_PASSWORD, ""),
                m.getOrDefault(SmtpSettingsDTO.KEY_FROM_ADDRESS, "noreply@codecrow.io"),
                m.getOrDefault(SmtpSettingsDTO.KEY_FROM_NAME, "CodeCrow"),
                Boolean.parseBoolean(m.getOrDefault(SmtpSettingsDTO.KEY_STARTTLS, "true"))
        );
    }

    @Override
    public GoogleOAuthSettingsDTO getGoogleOAuthSettings() {
        Map<String, String> m = getRawValues(ESiteSettingsGroup.GOOGLE_OAUTH);
        return new GoogleOAuthSettingsDTO(
                m.getOrDefault(GoogleOAuthSettingsDTO.KEY_CLIENT_ID, "")
        );
    }

    @Override
    public BaseUrlSettingsDTO getBaseUrlSettings() {
        Map<String, String> m = getRawValues(ESiteSettingsGroup.BASE_URLS);
        return new BaseUrlSettingsDTO(
                m.getOrDefault(BaseUrlSettingsDTO.KEY_BASE_URL, "http://localhost:8080"),
                m.getOrDefault(BaseUrlSettingsDTO.KEY_FRONTEND_URL, "http://localhost:5173")
        );
    }

    // ─────────────── Generic Read / Write ───────────────

    @Override
    public Map<String, String> getSettingsGroupMasked(ESiteSettingsGroup group) {
        Map<String, String> raw = getRawValues(group);
        Map<String, String> masked = new LinkedHashMap<>();
        for (Map.Entry<String, String> entry : raw.entrySet()) {
            if (SECRET_KEYS.contains(entry.getKey()) && entry.getValue() != null && !entry.getValue().isEmpty()) {
                masked.put(entry.getKey(), maskSecret(entry.getValue()));
            } else {
                masked.put(entry.getKey(), entry.getValue());
            }
        }
        return masked;
    }

    @Override
    @Transactional
    public void updateSettingsGroup(ESiteSettingsGroup group, Map<String, String> values) {
        for (Map.Entry<String, String> entry : values.entrySet()) {
            String key = entry.getKey();
            String value = entry.getValue();

            // Skip masked placeholder values — don't overwrite existing secrets
            if (value != null && value.contains("••••")) {
                continue;
            }

            SiteSettings setting = repository
                    .findByConfigGroupAndConfigKey(group, key)
                    .orElseGet(() -> {
                        SiteSettings s = new SiteSettings();
                        s.setConfigGroup(group);
                        s.setConfigKey(key);
                        return s;
                    });

            boolean isSecret = SECRET_KEYS.contains(key);
            setting.setSecret(isSecret);

            if (isSecret && value != null && !value.isEmpty()) {
                try {
                    setting.setConfigValue(encryptionService.encrypt(value));
                } catch (GeneralSecurityException e) {
                    throw new RuntimeException("Failed to encrypt setting: " + group + "." + key, e);
                }
            } else {
                setting.setConfigValue(value);
            }

            repository.save(setting);
        }
        log.info("Updated site settings group: {}", group);
    }

    @Override
    public String getValue(ESiteSettingsGroup group, String key) {
        return repository.findByConfigGroupAndConfigKey(group, key)
                .map(s -> {
                    if (s.isSecret() && s.getConfigValue() != null && !s.getConfigValue().isEmpty()) {
                        try {
                            return encryptionService.decrypt(s.getConfigValue());
                        } catch (GeneralSecurityException e) {
                            log.error("Failed to decrypt setting {}.{}", group, key, e);
                            return null;
                        }
                    }
                    return s.getConfigValue();
                })
                .orElse(null);
    }

    @Override
    public boolean isGroupConfigured(ESiteSettingsGroup group) {
        return repository.existsByConfigGroup(group);
    }

    @Override
    public ConfigurationStatusDTO getConfigurationStatus() {
        Map<ESiteSettingsGroup, Boolean> groups = new EnumMap<>(ESiteSettingsGroup.class);
        for (ESiteSettingsGroup g : ESiteSettingsGroup.values()) {
            groups.put(g, isGroupConfigured(g));
        }
        // Setup is complete when all required groups (BASE_URLS, EMBEDDING) are configured.
        // VCS providers are optional — users can add them later from the admin panel.
        boolean allRequired = REQUIRED_GROUPS.stream().allMatch(g -> groups.getOrDefault(g, false));
        boolean setupComplete = allRequired;

        return new ConfigurationStatusDTO(groups, setupComplete);
    }

    // ─────────────── Internals ───────────────

    /**
     * Returns decrypted raw values for a group.
     */
    private Map<String, String> getRawValues(ESiteSettingsGroup group) {
        return repository.findByConfigGroup(group).stream()
                .collect(Collectors.toMap(
                        SiteSettings::getConfigKey,
                        s -> {
                            if (s.isSecret() && s.getConfigValue() != null && !s.getConfigValue().isEmpty()) {
                                try {
                                    return encryptionService.decrypt(s.getConfigValue());
                                } catch (GeneralSecurityException e) {
                                    log.error("Failed to decrypt {}.{}", group, s.getConfigKey(), e);
                                    return "";
                                }
                            }
                            return s.getConfigValue() != null ? s.getConfigValue() : "";
                        },
                        (v1, v2) -> v2,
                        LinkedHashMap::new
                ));
    }

    /**
     * Mask a secret value for display: show first 4 and last 4 characters.
     */
    private static String maskSecret(String value) {
        if (value == null || value.length() <= 8) {
            return "••••••••";
        }
        return value.substring(0, 4) + "••••" + value.substring(value.length() - 4);
    }

    // ─────────────── Secure file download ───────────────

    /** Maximum private key file size: 16 KB (PEM files are typically 1–3 KB). */
    private static final long MAX_KEY_FILE_SIZE = 16 * 1024;

    @Override
    public Resource downloadPrivateKeyFile() {
        // 1. Read the stored private-key-path from the database
        String rawPath = getValue(ESiteSettingsGroup.VCS_GITHUB,
                GitHubSettingsDTO.KEY_PRIVATE_KEY_PATH);

        if (rawPath == null || rawPath.isBlank()) {
            throw new IllegalStateException(
                    "No GitHub private key path is configured. " +
                    "Set it in Site Administration → GitHub before downloading.");
        }

        // 2. Security checks
        validateKeyFilePath(rawPath);

        Path filePath = Path.of(rawPath).toAbsolutePath().normalize();

        // 3. Re-check after normalization (catches symlink tricks)
        if (!filePath.toString().endsWith(".pem")) {
            throw new SecurityException("Resolved path does not point to a .pem file.");
        }

        // 4. File must exist and be readable
        if (!Files.exists(filePath) || !Files.isRegularFile(filePath)) {
            throw new RuntimeException(
                    new FileNotFoundException("Private key file not found: " + filePath));
        }

        if (!Files.isReadable(filePath)) {
            throw new SecurityException("Private key file is not readable by the server process.");
        }

        // 5. Size limit — PEM files are tiny, reject anything suspiciously large
        try {
            long size = Files.size(filePath);
            if (size > MAX_KEY_FILE_SIZE) {
                throw new SecurityException(
                        "File exceeds maximum allowed size of " + MAX_KEY_FILE_SIZE + " bytes. " +
                        "PEM private keys are typically 1–3 KB.");
            }
            if (size == 0) {
                throw new SecurityException("Private key file is empty.");
            }
        } catch (java.io.IOException e) {
            throw new RuntimeException("Failed to read file metadata", e);
        }

        log.info("Admin downloading private key file: {}", filePath);
        return new FileSystemResource(filePath);
    }

    /**
     * Strict validation of the private key path before any filesystem access.
     *
     * <ul>
     *   <li>Must end with {@code .pem}</li>
     *   <li>Must not contain path-traversal sequences ({@code ..})</li>
     *   <li>Must not contain null bytes</li>
     *   <li>Must be an absolute path</li>
     * </ul>
     */
    private static void validateKeyFilePath(String path) {
        // Null-byte injection
        if (path.contains("\0")) {
            throw new SecurityException("Invalid path: contains null bytes.");
        }

        // Path traversal
        if (path.contains("..")) {
            throw new SecurityException("Invalid path: path traversal (..) is not allowed.");
        }

        // Must be .pem
        if (!path.toLowerCase().endsWith(".pem")) {
            throw new SecurityException(
                    "Only .pem files can be downloaded. The configured path does not end with .pem");
        }

        // Must be absolute
        if (!path.startsWith("/")) {
            throw new SecurityException("Private key path must be absolute (start with /).");
        }

        // Block obviously dangerous paths
        String lower = path.toLowerCase();
        if (lower.startsWith("/etc/shadow") || lower.startsWith("/etc/passwd") ||
            lower.startsWith("/proc/") || lower.startsWith("/sys/")) {
            throw new SecurityException("Access to system paths is not allowed.");
        }
    }
}
