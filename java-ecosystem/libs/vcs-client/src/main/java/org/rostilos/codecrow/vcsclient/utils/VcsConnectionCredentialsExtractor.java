package org.rostilos.codecrow.vcsclient.utils;

import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.security.GeneralSecurityException;

/**
 * Single source of truth for extracting VCS connection credentials.
 * Handles all provider types (GitHub, GitLab, Bitbucket) and all connection types
 * (OAUTH_MANUAL, APP, CONNECT_APP, GITHUB_APP, PERSONAL_TOKEN, REPOSITORY_TOKEN, APPLICATION).
 */
public class VcsConnectionCredentialsExtractor {
    
    private static final Logger log = LoggerFactory.getLogger(VcsConnectionCredentialsExtractor.class);
    
    private final TokenEncryptionService tokenEncryptionService;

    public VcsConnectionCredentialsExtractor(TokenEncryptionService tokenEncryptionService) {
        this.tokenEncryptionService = tokenEncryptionService;
    }

    /**
     * Extract credentials from a VcsConnection.
     * This is the main method that handles all provider and connection type combinations.
     *
     * @param vcsConnection The VcsConnection to extract credentials from
     * @return VcsConnectionCredentials containing the extracted credentials
     * @throws GeneralSecurityException if decryption fails
     */
    public VcsConnectionCredentials extractCredentials(VcsConnection vcsConnection) throws GeneralSecurityException {
        if (vcsConnection == null) {
            throw new IllegalArgumentException("VcsConnection cannot be null");
        }
        
        EVcsProvider provider = vcsConnection.getProviderType();
        EVcsConnectionType connectionType = vcsConnection.getConnectionType();
        
        if (provider == null) {
            throw new IllegalArgumentException("VcsConnection provider type cannot be null");
        }
        
        if (connectionType == null) {
            throw new IllegalArgumentException("VcsConnection type cannot be null");
        }
        
        log.debug("Extracting credentials for provider={}, connectionType={}", provider, connectionType);
        
        String oAuthClient = null;
        String oAuthSecret = null;
        String accessToken = null;
        
        switch (connectionType) {
            case OAUTH_MANUAL -> {
                // Bitbucket Cloud OAuth consumer credentials
                if (provider == EVcsProvider.BITBUCKET_CLOUD && 
                    vcsConnection.getConfiguration() instanceof BitbucketCloudConfig config) {
                    oAuthClient = decryptIfNeeded(config.oAuthKey());
                    oAuthSecret = decryptIfNeeded(config.oAuthToken());
                } else {
                    log.warn("OAUTH_MANUAL connection type but no BitbucketCloudConfig found for provider {}", provider);
                }
            }
            
            case APP, CONNECT_APP -> {
                // Bitbucket App/Connect App - uses encrypted accessToken on VcsConnection
                accessToken = decryptStoredAccessToken(vcsConnection);
            }
            
            case GITHUB_APP -> {
                // GitHub App installation - uses encrypted accessToken on VcsConnection
                accessToken = decryptStoredAccessToken(vcsConnection);
            }
            
            case APPLICATION -> {
                // GitLab OAuth Application - uses encrypted accessToken on VcsConnection
                accessToken = decryptStoredAccessToken(vcsConnection);
            }
            
            case PERSONAL_TOKEN -> {
                // Personal Access Token - stored in config (GitHub or GitLab)
                accessToken = extractTokenFromConfig(vcsConnection);
            }
            
            case REPOSITORY_TOKEN -> {
                // Repository-scoped token (GitLab Project Access Token, etc.) - stored in config
                accessToken = extractTokenFromConfig(vcsConnection);
            }
            
            case ACCESS_TOKEN -> {
                // Generic access token (e.g., Bitbucket Server) - uses stored accessToken
                accessToken = decryptStoredAccessToken(vcsConnection);
            }
            
            default -> {
                log.warn("Unknown connection type: {}. Attempting to extract from stored accessToken.", connectionType);
                accessToken = decryptStoredAccessToken(vcsConnection);
            }
        }
        
        String vcsProviderString = getVcsProviderString(provider);
        
        return new VcsConnectionCredentials(oAuthClient, oAuthSecret, accessToken, vcsProviderString, provider, connectionType);
    }

    /**
     * Convenience method - same as extractCredentials but with backwards compatible name.
     */
    public VcsConnectionCredentials extractCredentialsFromConnection(VcsConnection vcsConnection) throws GeneralSecurityException {
        return extractCredentials(vcsConnection);
    }

    /**
     * Decrypt the stored accessToken from VcsConnection.
     */
    private String decryptStoredAccessToken(VcsConnection vcsConnection) throws GeneralSecurityException {
        String encryptedToken = vcsConnection.getAccessToken();
        if (encryptedToken == null || encryptedToken.isBlank()) {
            log.warn("No stored accessToken found for connection id={}, type={}", 
                    vcsConnection.getId(), vcsConnection.getConnectionType());
            return null;
        }
        return tokenEncryptionService.decrypt(encryptedToken);
    }

    /**
     * Extract access token from the connection's configuration object.
     * Supports GitHubConfig and GitLabConfig.
     */
    private String extractTokenFromConfig(VcsConnection vcsConnection) {
        Object config = vcsConnection.getConfiguration();
        
        if (config instanceof GitLabConfig gitLabConfig) {
            return gitLabConfig.accessToken();
        }
        
        if (config instanceof GitHubConfig gitHubConfig) {
            return gitHubConfig.accessToken();
        }
        
        // Fallback: try to get from stored accessToken (some implementations store it there)
        if (vcsConnection.getAccessToken() != null && !vcsConnection.getAccessToken().isBlank()) {
            try {
                return tokenEncryptionService.decrypt(vcsConnection.getAccessToken());
            } catch (GeneralSecurityException e) {
                log.warn("Failed to decrypt stored accessToken for connection id={}: {}", 
                        vcsConnection.getId(), e.getMessage());
            }
        }
        
        log.warn("Could not extract token from config for connection id={}, configType={}", 
                vcsConnection.getId(), config != null ? config.getClass().getSimpleName() : "null");
        return null;
    }

    /**
     * Decrypt a value if tokenEncryptionService is available.
     */
    private String decryptIfNeeded(String value) throws GeneralSecurityException {
        if (value == null || value.isBlank()) {
            return null;
        }
        return tokenEncryptionService.decrypt(value);
    }

    /**
     * Get the VCS provider string used for VCS MCP server client selection.
     * This is the canonical mapping from EVcsProvider to the string identifier used throughout the system.
     *
     * @param provider The EVcsProvider enum value
     * @return The string identifier (e.g., "github", "gitlab", "bitbucket_cloud")
     */
    public static String getVcsProviderString(EVcsProvider provider) {
        if (provider == null) {
            return "bitbucket_cloud"; // Default fallback
        }
        
        return switch (provider) {
            case GITHUB -> "github";
            case GITLAB -> "gitlab";
            case BITBUCKET_CLOUD -> "bitbucket_cloud";
            case BITBUCKET_SERVER -> "bitbucket_server";
        };
    }

    /**
     * Check if the credentials include OAuth client credentials (for OAuth flow).
     */
    public static boolean hasOAuthCredentials(VcsConnectionCredentials credentials) {
        return credentials != null && 
               credentials.oAuthClient() != null && !credentials.oAuthClient().isBlank() &&
               credentials.oAuthSecret() != null && !credentials.oAuthSecret().isBlank();
    }

    /**
     * Check if the credentials include an access token.
     */
    public static boolean hasAccessToken(VcsConnectionCredentials credentials) {
        return credentials != null && 
               credentials.accessToken() != null && !credentials.accessToken().isBlank();
    }

    /**
     * Check if any valid credentials are available.
     */
    public static boolean hasValidCredentials(VcsConnectionCredentials credentials) {
        return hasOAuthCredentials(credentials) || hasAccessToken(credentials);
    }

    /**
     * Record containing extracted VCS connection credentials.
     * Includes all possible credential types plus metadata about the connection.
     */
    public record VcsConnectionCredentials(
            String oAuthClient,
            String oAuthSecret,
            String accessToken,
            String vcsProviderString,
            EVcsProvider provider,
            EVcsConnectionType connectionType
    ) {
        /**
         * Backwards compatible constructor without provider info.
         */
        public VcsConnectionCredentials(String oAuthClient, String oAuthSecret, String accessToken) {
            this(oAuthClient, oAuthSecret, accessToken, null, null, null);
        }
        
        /**
         * Check if this credentials object has OAuth client credentials.
         */
        public boolean hasOAuthCredentials() {
            return VcsConnectionCredentialsExtractor.hasOAuthCredentials(this);
        }
        
        /**
         * Check if this credentials object has an access token.
         */
        public boolean hasAccessToken() {
            return VcsConnectionCredentialsExtractor.hasAccessToken(this);
        }
        
        /**
         * Check if any valid credentials are available.
         */
        public boolean hasValidCredentials() {
            return VcsConnectionCredentialsExtractor.hasValidCredentials(this);
        }
    }
}
