package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.dto.admin.*;
import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;
import org.rostilos.codecrow.core.model.admin.SiteSettings;
import org.rostilos.codecrow.core.persistence.repository.admin.SiteSettingsRepository;
import org.rostilos.codecrow.core.security.TokenEncryptionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Unified site-settings provider for all modules (web-server, pipeline-agent, vcs-client).
 * <p>
 * Resolution order for each setting:
 * <ol>
 *   <li>{@code @Value} from application.properties / environment variable — if non-blank, wins.</li>
 *   <li>Database ({@code site_settings} table) — fallback when the property is empty.</li>
 * </ol>
 * <p>
 * This means:
 * <ul>
 *   <li><b>Cloud / SaaS:</b> set values in application.properties → {@code @Value} always wins, DB is never queried.</li>
 *   <li><b>Self-hosted:</b> leave properties empty → admin panel writes to DB, this service reads from DB.</li>
 * </ul>
 */
@Service
public class SiteSettingsProvider {

    private static final Logger log = LoggerFactory.getLogger(SiteSettingsProvider.class);

    // ── Keys whose DB values are encrypted at rest ──
    private static final Set<String> SECRET_KEYS = new HashSet<>(Arrays.asList(
            BitbucketSettingsDTO.KEY_CLIENT_SECRET,
            BitbucketConnectSettingsDTO.KEY_CLIENT_SECRET,
            LlmSyncSettingsDTO.KEY_OPENROUTER_API_KEY,
            LlmSyncSettingsDTO.KEY_OPENAI_API_KEY,
            LlmSyncSettingsDTO.KEY_ANTHROPIC_API_KEY,
            LlmSyncSettingsDTO.KEY_GOOGLE_API_KEY,
            EmbeddingSettingsDTO.KEY_OPENROUTER_API_KEY,
            SmtpSettingsDTO.KEY_PASSWORD,
            GitLabSettingsDTO.KEY_CLIENT_SECRET,
            GitHubSettingsDTO.KEY_WEBHOOK_SECRET,
            GitHubSettingsDTO.KEY_OAUTH_CLIENT_SECRET
    ));

    private static final Set<ESiteSettingsGroup> REQUIRED_GROUPS = Set.of(
            ESiteSettingsGroup.BASE_URLS
    );

    // ═══════════════════════════════════════════════════════════════════
    //  @Value fields — populated from application.properties / env vars.
    //  All default to "" so that blank = "not set, fall through to DB".
    // ═══════════════════════════════════════════════════════════════════

    // ── Bitbucket OAuth App ──
    @Value("${codecrow.bitbucket.app.client-id:}")
    private String propBbClientId;
    @Value("${codecrow.bitbucket.app.client-secret:}")
    private String propBbClientSecret;

    // ── Bitbucket Connect App ──
    @Value("${codecrow.bitbucket.connect.client-id:}")
    private String propBbConnectClientId;
    @Value("${codecrow.bitbucket.connect.client-secret:}")
    private String propBbConnectClientSecret;

    // ── GitHub App ──
    @Value("${codecrow.github.app.id:}")
    private String propGhAppId;
    @Value("${codecrow.github.app.private-key-path:}")
    private String propGhPrivateKeyPath;
    @Value("${codecrow.github.app.webhook-secret:}")
    private String propGhWebhookSecret;
    @Value("${codecrow.github.app.slug:}")
    private String propGhSlug;
    @Value("${codecrow.github.oauth.client-id:}")
    private String propGhOAuthClientId;
    @Value("${codecrow.github.oauth.client-secret:}")
    private String propGhOAuthClientSecret;

    // ── GitLab OAuth ──
    @Value("${codecrow.gitlab.oauth.client-id:}")
    private String propGlClientId;
    @Value("${codecrow.gitlab.oauth.client-secret:}")
    private String propGlClientSecret;
    @Value("${codecrow.gitlab.oauth.base-url:}")
    private String propGlBaseUrl;

    // ── LLM Sync ──
    @Value("${llm.sync.openrouter.api-key:}")
    private String propLlmOpenrouterKey;
    @Value("${llm.sync.openai.api-key:}")
    private String propLlmOpenaiKey;
    @Value("${llm.sync.anthropic.api-key:}")
    private String propLlmAnthropicKey;
    @Value("${llm.sync.google.api-key:}")
    private String propLlmGoogleKey;

    // ── Embedding ──
    @Value("${codecrow.embedding.provider:}")
    private String propEmbeddingProvider;
    @Value("${codecrow.embedding.ollama.base-url:}")
    private String propEmbeddingOllamaBaseUrl;
    @Value("${codecrow.embedding.ollama.model:}")
    private String propEmbeddingOllamaModel;
    @Value("${codecrow.embedding.openrouter.api-key:}")
    private String propEmbeddingOpenrouterKey;
    @Value("${codecrow.embedding.openrouter.model:}")
    private String propEmbeddingOpenrouterModel;

    // ── SMTP ──
    @Value("${codecrow.smtp.enabled:}")
    private String propSmtpEnabled;
    @Value("${spring.mail.host:}")
    private String propSmtpHost;
    @Value("${spring.mail.port:}")
    private String propSmtpPort;
    @Value("${spring.mail.username:}")
    private String propSmtpUsername;
    @Value("${spring.mail.password:}")
    private String propSmtpPassword;
    @Value("${codecrow.smtp.from-address:}")
    private String propSmtpFromAddress;
    @Value("${codecrow.smtp.from-name:}")
    private String propSmtpFromName;
    @Value("${codecrow.smtp.starttls:}")
    private String propSmtpStarttls;

    // ── Google OAuth ──
    @Value("${codecrow.oauth.google.client-id:}")
    private String propGoogleClientId;

    // ── Base URLs ──
    @Value("${codecrow.web.base.url:}")
    private String propBaseUrl;
    @Value("${codecrow.frontend-url:}")
    private String propFrontendUrl;
    @Value("${codecrow.webhook.base-url:}")
    private String propWebhookBaseUrl;

    // ═══════════════════════════════════════════════════════════════════

    private final SiteSettingsRepository repository;
    private final TokenEncryptionService encryptionService;

    public SiteSettingsProvider(SiteSettingsRepository repository,
                                TokenEncryptionService encryptionService) {
        this.repository = repository;
        this.encryptionService = encryptionService;
    }

    // ─────────────── Typed Getters (env-first, DB-fallback) ───────────────

    public BitbucketSettingsDTO getBitbucketSettings() {
        Map<String, String> db = getDbValues(ESiteSettingsGroup.VCS_BITBUCKET);
        return new BitbucketSettingsDTO(
                resolve(propBbClientId, db, BitbucketSettingsDTO.KEY_CLIENT_ID, ""),
                resolve(propBbClientSecret, db, BitbucketSettingsDTO.KEY_CLIENT_SECRET, "")
        );
    }

    public BitbucketConnectSettingsDTO getBitbucketConnectSettings() {
        Map<String, String> db = getDbValues(ESiteSettingsGroup.VCS_BITBUCKET_CONNECT);
        return new BitbucketConnectSettingsDTO(
                resolve(propBbConnectClientId, db, BitbucketConnectSettingsDTO.KEY_CLIENT_ID, ""),
                resolve(propBbConnectClientSecret, db, BitbucketConnectSettingsDTO.KEY_CLIENT_SECRET, "")
        );
    }

    public GitHubSettingsDTO getGitHubSettings() {
        Map<String, String> db = getDbValues(ESiteSettingsGroup.VCS_GITHUB);
        return new GitHubSettingsDTO(
                resolve(propGhAppId, db, GitHubSettingsDTO.KEY_APP_ID, ""),
                resolve(propGhPrivateKeyPath, db, GitHubSettingsDTO.KEY_PRIVATE_KEY_PATH, ""),
                resolve(propGhWebhookSecret, db, GitHubSettingsDTO.KEY_WEBHOOK_SECRET, ""),
                resolve(propGhSlug, db, GitHubSettingsDTO.KEY_SLUG, ""),
                resolve(propGhOAuthClientId, db, GitHubSettingsDTO.KEY_OAUTH_CLIENT_ID, ""),
                resolve(propGhOAuthClientSecret, db, GitHubSettingsDTO.KEY_OAUTH_CLIENT_SECRET, "")
        );
    }

    public GitLabSettingsDTO getGitLabSettings() {
        Map<String, String> db = getDbValues(ESiteSettingsGroup.VCS_GITLAB);
        return new GitLabSettingsDTO(
                resolve(propGlClientId, db, GitLabSettingsDTO.KEY_CLIENT_ID, ""),
                resolve(propGlClientSecret, db, GitLabSettingsDTO.KEY_CLIENT_SECRET, ""),
                resolve(propGlBaseUrl, db, GitLabSettingsDTO.KEY_BASE_URL, "https://gitlab.com")
        );
    }

    public LlmSyncSettingsDTO getLlmSyncSettings() {
        Map<String, String> db = getDbValues(ESiteSettingsGroup.LLM_SYNC);
        return new LlmSyncSettingsDTO(
                resolve(propLlmOpenrouterKey, db, LlmSyncSettingsDTO.KEY_OPENROUTER_API_KEY, ""),
                resolve(propLlmOpenaiKey, db, LlmSyncSettingsDTO.KEY_OPENAI_API_KEY, ""),
                resolve(propLlmAnthropicKey, db, LlmSyncSettingsDTO.KEY_ANTHROPIC_API_KEY, ""),
                resolve(propLlmGoogleKey, db, LlmSyncSettingsDTO.KEY_GOOGLE_API_KEY, "")
        );
    }

    public EmbeddingSettingsDTO getEmbeddingSettings() {
        Map<String, String> db = getDbValues(ESiteSettingsGroup.EMBEDDING);
        return new EmbeddingSettingsDTO(
                resolve(propEmbeddingProvider, db, EmbeddingSettingsDTO.KEY_PROVIDER, "ollama"),
                resolve(propEmbeddingOllamaBaseUrl, db, EmbeddingSettingsDTO.KEY_OLLAMA_BASE_URL, "http://localhost:11434"),
                resolve(propEmbeddingOllamaModel, db, EmbeddingSettingsDTO.KEY_OLLAMA_MODEL, "qwen3-embedding:0.6b"),
                resolve(propEmbeddingOpenrouterKey, db, EmbeddingSettingsDTO.KEY_OPENROUTER_API_KEY, ""),
                resolve(propEmbeddingOpenrouterModel, db, EmbeddingSettingsDTO.KEY_OPENROUTER_MODEL, "qwen/qwen3-embedding-8b")
        );
    }

    public SmtpSettingsDTO getSmtpSettings() {
        Map<String, String> db = getDbValues(ESiteSettingsGroup.SMTP);
        return new SmtpSettingsDTO(
                Boolean.parseBoolean(resolve(propSmtpEnabled, db, SmtpSettingsDTO.KEY_ENABLED, "false")),
                resolve(propSmtpHost, db, SmtpSettingsDTO.KEY_HOST, ""),
                Integer.parseInt(resolve(propSmtpPort, db, SmtpSettingsDTO.KEY_PORT, "587")),
                resolve(propSmtpUsername, db, SmtpSettingsDTO.KEY_USERNAME, ""),
                resolve(propSmtpPassword, db, SmtpSettingsDTO.KEY_PASSWORD, ""),
                resolve(propSmtpFromAddress, db, SmtpSettingsDTO.KEY_FROM_ADDRESS, "noreply@codecrow.io"),
                resolve(propSmtpFromName, db, SmtpSettingsDTO.KEY_FROM_NAME, "CodeCrow"),
                Boolean.parseBoolean(resolve(propSmtpStarttls, db, SmtpSettingsDTO.KEY_STARTTLS, "true"))
        );
    }

    public GoogleOAuthSettingsDTO getGoogleOAuthSettings() {
        Map<String, String> db = getDbValues(ESiteSettingsGroup.GOOGLE_OAUTH);
        return new GoogleOAuthSettingsDTO(
                resolve(propGoogleClientId, db, GoogleOAuthSettingsDTO.KEY_CLIENT_ID, "")
        );
    }

    public BaseUrlSettingsDTO getBaseUrlSettings() {
        Map<String, String> db = getDbValues(ESiteSettingsGroup.BASE_URLS);
        return new BaseUrlSettingsDTO(
                resolve(propBaseUrl, db, BaseUrlSettingsDTO.KEY_BASE_URL, "http://localhost:8081"),
                resolve(propFrontendUrl, db, BaseUrlSettingsDTO.KEY_FRONTEND_URL, "http://localhost:8080"),
                resolve(propWebhookBaseUrl, db, BaseUrlSettingsDTO.KEY_WEBHOOK_BASE_URL, "http://localhost:8082")
        );
    }

    // ─────────────── Admin Panel Operations ───────────────

    public Map<String, String> getSettingsGroupMasked(ESiteSettingsGroup group) {
        Map<String, String> raw = getDbValues(group);
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

    @Transactional
    public void updateSettingsGroup(ESiteSettingsGroup group, Map<String, String> values) {
        for (Map.Entry<String, String> entry : values.entrySet()) {
            String key = entry.getKey();
            String value = entry.getValue();

            // Skip masked placeholder values (admin didn't change this secret)
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

    public boolean isGroupConfigured(ESiteSettingsGroup group) {
        return repository.existsByConfigGroup(group);
    }

    public ConfigurationStatusDTO getConfigurationStatus() {
        Map<ESiteSettingsGroup, Boolean> groups = new EnumMap<>(ESiteSettingsGroup.class);
        for (ESiteSettingsGroup g : ESiteSettingsGroup.values()) {
            groups.put(g, isGroupConfigured(g));
        }
        boolean setupComplete = REQUIRED_GROUPS.stream()
                .allMatch(g -> groups.getOrDefault(g, false));
        return new ConfigurationStatusDTO(groups, setupComplete);
    }

    // ─────────────── Private Key File Operations ───────────────

    private static final long MAX_KEY_FILE_SIZE = 16 * 1024;
    private static final String KEY_UPLOAD_DIR = "/app/data/keys";

    public Resource downloadPrivateKeyFile() {
        String rawPath = getGitHubSettings().privateKeyPath();
        if (rawPath == null || rawPath.isBlank()) {
            throw new IllegalStateException(
                    "No GitHub private key path is configured. " +
                    "Set it in Site Administration → GitHub before downloading.");
        }
        validateKeyFilePath(rawPath);
        Path filePath = Path.of(rawPath).toAbsolutePath().normalize();
        if (!filePath.toString().endsWith(".pem")) {
            throw new SecurityException("Resolved path does not point to a .pem file.");
        }
        if (!Files.exists(filePath) || !Files.isRegularFile(filePath)) {
            throw new RuntimeException(
                    new FileNotFoundException("Private key file not found: " + filePath));
        }
        if (!Files.isReadable(filePath)) {
            throw new SecurityException("Private key file is not readable by the server process.");
        }
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
        } catch (IOException e) {
            throw new RuntimeException("Failed to read file metadata", e);
        }
        log.info("Admin downloading private key file: {}", filePath);
        return new FileSystemResource(filePath);
    }

    /**
     * Upload a private key file. Accepts raw bytes + original filename
     * so that this method has no dependency on Spring Web's MultipartFile.
     * The controller layer converts MultipartFile → bytes before calling this.
     */
    public String uploadPrivateKeyFile(String originalFilename, InputStream inputStream, long fileSize) {
        if (originalFilename == null || !originalFilename.toLowerCase().endsWith(".pem")) {
            throw new SecurityException("Only .pem files are accepted.");
        }
        if (fileSize > MAX_KEY_FILE_SIZE) {
            throw new IllegalArgumentException(
                    "File exceeds maximum allowed size of " + MAX_KEY_FILE_SIZE + " bytes. " +
                    "PEM private keys are typically 1–3 KB.");
        }
        if (fileSize == 0) {
            throw new IllegalArgumentException("File is empty.");
        }
        try {
            Path uploadDir = Path.of(KEY_UPLOAD_DIR);
            Files.createDirectories(uploadDir);
            Path targetPath = uploadDir.resolve("github-app-private-key.pem").toAbsolutePath().normalize();
            if (!targetPath.startsWith(uploadDir.toAbsolutePath().normalize())) {
                throw new SecurityException("Path traversal detected.");
            }
            Files.copy(inputStream, targetPath, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            log.info("Admin uploaded private key file to: {}", targetPath);
            return targetPath.toString();
        } catch (IOException e) {
            throw new RuntimeException("Failed to save uploaded private key file.", e);
        }
    }

    // ─────────────── Internal Helpers ───────────────

    /**
     * Core resolution logic: if the {@code @Value} property is non-blank, return it.
     * Otherwise, return the DB value (or the default).
     */
    private static String resolve(String propValue, Map<String, String> dbValues,
                                   String dbKey, String defaultValue) {
        if (propValue != null && !propValue.isBlank()) {
            return propValue;
        }
        String dbVal = dbValues.get(dbKey);
        if (dbVal != null && !dbVal.isBlank()) {
            return dbVal;
        }
        return defaultValue;
    }

    /**
     * Read all key-value pairs for a settings group from the DB,
     * decrypting secrets automatically.
     */
    private Map<String, String> getDbValues(ESiteSettingsGroup group) {
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

    private static String maskSecret(String value) {
        if (value == null || value.length() <= 8) {
            return "••••••••";
        }
        return value.substring(0, 4) + "••••" + value.substring(value.length() - 4);
    }

    private static void validateKeyFilePath(String path) {
        if (path.contains("\0")) {
            throw new SecurityException("Invalid path: contains null bytes.");
        }
        if (path.contains("..")) {
            throw new SecurityException("Invalid path: path traversal (..) is not allowed.");
        }
        if (!path.toLowerCase().endsWith(".pem")) {
            throw new SecurityException(
                    "Only .pem files can be downloaded. The configured path does not end with .pem");
        }
        if (!path.startsWith("/")) {
            throw new SecurityException("Private key path must be absolute (start with /).");
        }
        String lower = path.toLowerCase();
        if (lower.startsWith("/etc/shadow") || lower.startsWith("/etc/passwd") ||
            lower.startsWith("/proc/") || lower.startsWith("/sys/")) {
            throw new SecurityException("Access to system paths is not allowed.");
        }
    }
}
