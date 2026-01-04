package org.rostilos.codecrow.vcsclient;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.jsonwebtoken.Jwts;
import okhttp3.FormBody;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import org.rostilos.codecrow.core.model.vcs.BitbucketConnectInstallation;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.persistence.repository.vcs.BitbucketConnectInstallationRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudClient;
import org.rostilos.codecrow.vcsclient.github.GitHubClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.crypto.SecretKey;
import io.jsonwebtoken.security.Keys;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.LocalDateTime;
import java.util.Base64;
import java.util.Optional;

/**
 * Unified VCS Client Provider.
 * 
 * This service provides authorized VCS clients for any provider and connection type.
 * It handles:
 * - OAuth2 bearer tokens (APP connections)
 * - OAuth Consumer credentials (OAUTH_MANUAL connections)
 * - Token refresh when needed
 * 
 * Usage:
 *   VcsClient client = vcsClientProvider.getClient(connectionId);
 *   // or
 *   VcsClient client = vcsClientProvider.getClient(connection);
 */
@Service
public class VcsClientProvider {
    
    private static final Logger log = LoggerFactory.getLogger(VcsClientProvider.class);
    private static final String BITBUCKET_TOKEN_URL = "https://bitbucket.org/site/oauth2/access_token";
    private static final String GITLAB_TOKEN_URL = "https://gitlab.com/oauth/token";
    private static final MediaType FORM_MEDIA_TYPE = MediaType.parse("application/x-www-form-urlencoded");
    private static final ObjectMapper objectMapper = new ObjectMapper();
    
    private final VcsConnectionRepository connectionRepository;
    private final BitbucketConnectInstallationRepository connectInstallationRepository;
    private final TokenEncryptionService encryptionService;
    private final HttpAuthorizedClientFactory httpClientFactory;
    
    @Value("${codecrow.bitbucket.app.client-id:}")
    private String bitbucketAppClientId;
    
    @Value("${codecrow.bitbucket.app.client-secret:}")
    private String bitbucketAppClientSecret;
    
    @Value("${codecrow.github.app.id:}")
    private String githubAppId;
    
    @Value("${codecrow.github.app.private-key-path:}")
    private String githubAppPrivateKeyPath;
    
    @Value("${codecrow.gitlab.oauth.client-id:}")
    private String gitlabOAuthClientId;
    
    @Value("${codecrow.gitlab.oauth.client-secret:}")
    private String gitlabOAuthClientSecret;
    
    @Value("${codecrow.gitlab.base-url:https://gitlab.com}")
    private String gitlabBaseUrl;

    public VcsClientProvider(
            VcsConnectionRepository connectionRepository,
            BitbucketConnectInstallationRepository connectInstallationRepository,
            TokenEncryptionService encryptionService,
            HttpAuthorizedClientFactory httpClientFactory
    ) {
        this.connectionRepository = connectionRepository;
        this.connectInstallationRepository = connectInstallationRepository;
        this.encryptionService = encryptionService;
        this.httpClientFactory = httpClientFactory;
    }
    
    /**
     * Get an authorized VCS client for the given connection ID.
     * 
     * @param connectionId the VCS connection ID
     * @return authorized VcsClient
     * @throws IllegalArgumentException if connection not found
     * @throws VcsClientException if client creation fails
     */
    public VcsClient getClient(Long connectionId) {
        VcsConnection connection = connectionRepository.findById(connectionId)
                .orElseThrow(() -> new IllegalArgumentException("VCS connection not found: " + connectionId));
        return getClient(connection);
    }
    
    /**
     * Get an authorized VCS client for the given connection ID within a workspace.
     * 
     * @param workspaceId the workspace ID
     * @param connectionId the VCS connection ID
     * @return authorized VcsClient
     * @throws IllegalArgumentException if connection not found or doesn't belong to workspace
     * @throws VcsClientException if client creation fails
     */
    public VcsClient getClient(Long workspaceId, Long connectionId) {
        VcsConnection connection = connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException(
                        "VCS connection not found: " + connectionId + " in workspace: " + workspaceId));
        return getClient(connection);
    }
    
    /**
     * Get an authorized VCS client for the given connection entity.
     * Automatically refreshes token if needed for APP connections.
     * 
     * @param connection the VCS connection entity
     * @return authorized VcsClient
     * @throws VcsClientException if client creation fails
     */
    public VcsClient getClient(VcsConnection connection) {
        try {
            // Refresh token if needed for APP connections
            VcsConnection activeConnection = ensureValidToken(connection);
            OkHttpClient httpClient = createHttpClient(activeConnection);
            return createVcsClient(activeConnection.getProviderType(), httpClient);
        } catch (GeneralSecurityException e) {
            throw new VcsClientException("Failed to decrypt credentials for connection: " + connection.getId(), e);
        }
    }
    
    /**
     * Get an authorized OkHttpClient for the given connection.
     * Automatically refreshes token if needed for APP connections.
     * Use this for low-level HTTP operations.
     * 
     * @param connectionId the VCS connection ID
     * @return authorized OkHttpClient
     */
    public OkHttpClient getHttpClient(Long connectionId) {
        VcsConnection connection = connectionRepository.findById(connectionId)
                .orElseThrow(() -> new IllegalArgumentException("VCS connection not found: " + connectionId));
        return getHttpClient(connection);
    }
    
    /**
     * Get an authorized OkHttpClient for the given connection within a workspace.
     * Automatically refreshes token if needed for APP connections.
     * 
     * @param workspaceId the workspace ID
     * @param connectionId the VCS connection ID
     * @return authorized OkHttpClient
     */
    public OkHttpClient getHttpClient(Long workspaceId, Long connectionId) {
        VcsConnection connection = connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException(
                        "VCS connection not found: " + connectionId + " in workspace: " + workspaceId));
        return getHttpClient(connection);
    }
    
    /**
     * Get an authorized OkHttpClient for the given connection entity.
     * Automatically refreshes token if needed for APP connections.
     * Use this when you already have the connection entity.
     * 
     * @param connection the VCS connection entity
     * @return authorized OkHttpClient
     */
    public OkHttpClient getHttpClient(VcsConnection connection) {
        try {
            log.debug("getHttpClient called for connection: id={}, type={}, provider={}, hasRefreshToken={}, tokenExpiresAt={}", 
                    connection.getId(), 
                    connection.getConnectionType(), 
                    connection.getProviderType(),
                    connection.getRefreshToken() != null && !connection.getRefreshToken().isBlank(),
                    connection.getTokenExpiresAt());
            
            // Refresh token if needed for APP connections
            VcsConnection activeConnection = ensureValidToken(connection);
            return createHttpClient(activeConnection);
        } catch (GeneralSecurityException e) {
            throw new VcsClientException("Failed to decrypt credentials for connection: " + connection.getId(), e);
        }
    }
    
    /**
     * Ensure the connection has a valid (non-expired) token.
     * Automatically refreshes tokens if they're about to expire.
     * Supports both APP and OAUTH_MANUAL connections with refresh tokens.
     * 
     * @param connection the VCS connection
     * @return the connection with valid tokens (may be refreshed)
     */
    private VcsConnection ensureValidToken(VcsConnection connection) {
        // Check if token needs refresh
        if (needsTokenRefresh(connection)) {
            log.info("Token for connection {} is expired or about to expire, refreshing...", connection.getId());
            return refreshToken(connection);
        }
        
        return connection;
    }
    
    /**
     * Check if a connection's token needs refresh.
     * 
     * @param connection the VCS connection
     * @return true if token is expired or will expire within 5 minutes
     */
    public boolean needsTokenRefresh(VcsConnection connection) {
        // Check if connection has a refresh token - required for token refresh
        if (connection.getRefreshToken() == null || connection.getRefreshToken().isBlank()) {
            // For APP connections without refresh token, check if it's a Connect App
            if (connection.getConnectionType() == EVcsConnectionType.APP) {
                // For Bitbucket Connect Apps, we can refresh using JWT (checked in refreshBitbucketConnection)
                // For GitHub Apps, we can refresh using the app credentials
                if (connection.getTokenExpiresAt() != null && 
                    connection.getTokenExpiresAt().isBefore(LocalDateTime.now().plusMinutes(5))) {
                    log.info("needsTokenRefresh: Connection {} (APP without refresh token) - token expired, needs refresh", 
                            connection.getId());
                    return true;
                }
            }
            log.debug("needsTokenRefresh: Connection {} has no refresh token, skipping refresh check", connection.getId());
            return false;
        }
        
        // If no expiration is set but we have a refresh token, assume token might be stale
        // This handles cases where tokenExpiresAt wasn't properly set during migration/import
        if (connection.getTokenExpiresAt() == null) {
            log.info("needsTokenRefresh: Connection {} has refresh token but no expiration time set, forcing refresh", 
                    connection.getId());
            return true;
        }
        
        // Refresh if token expires within 5 minutes
        boolean needsRefresh = connection.getTokenExpiresAt().isBefore(LocalDateTime.now().plusMinutes(5));
        if (needsRefresh) {
            log.info("needsTokenRefresh: Connection {} token expires at {}, needs refresh", 
                    connection.getId(), connection.getTokenExpiresAt());
        }
        return needsRefresh;
    }
    
    /**
     * Refresh the access token for a connection.
     * Handles both APP connections (Connect Apps, GitHub Apps) and OAUTH_MANUAL connections with refresh tokens.
     * 
     * @param connection the VCS connection
     * @return updated connection with new tokens
     * @throws VcsClientException if refresh fails
     */
    @Transactional
    public VcsConnection refreshToken(VcsConnection connection) {
        log.info("Refreshing access token for connection: {} (provider: {}, type: {})", 
                connection.getId(), connection.getProviderType(), connection.getConnectionType());
        
        try {
            return switch (connection.getProviderType()) {
                case BITBUCKET_CLOUD -> refreshBitbucketConnection(connection);
                case GITHUB -> refreshGitHubAppConnection(connection);
                case GITLAB -> refreshGitLabConnection(connection);
                default -> throw new VcsClientException("Token refresh not supported for provider: " + connection.getProviderType());
            };
        } catch (GeneralSecurityException e) {
            throw new VcsClientException("Failed to encrypt/decrypt tokens for connection: " + connection.getId(), e);
        } catch (IOException e) {
            throw new VcsClientException("Failed to refresh token for connection: " + connection.getId(), e);
        } catch (Exception e) {
            throw new VcsClientException("Failed to refresh token for connection: " + connection.getId(), e);
        }
    }
    
    /**
     * Refresh Bitbucket Cloud connection.
     * For Connect Apps (APP type without refresh token), uses JWT Bearer grant.
     * For OAuth apps with refresh token, uses standard refresh_token grant.
     */
    private VcsConnection refreshBitbucketConnection(VcsConnection connection) 
            throws GeneralSecurityException, IOException {
        
        // Check if this is a Connect App connection (linked to BitbucketConnectInstallation)
        Optional<BitbucketConnectInstallation> installationOpt = 
                connectInstallationRepository.findByVcsConnection_Id(connection.getId());
        
        if (installationOpt.isPresent()) {
            // Use JWT Bearer grant for Connect Apps
            return refreshBitbucketConnectAppConnection(connection, installationOpt.get());
        }
        
        // Standard OAuth refresh token flow
        if (connection.getRefreshToken() == null) {
            throw new VcsClientException("No refresh token available for connection: " + connection.getId());
        }
        
        String decryptedRefreshToken = encryptionService.decrypt(connection.getRefreshToken());
        TokenResponse newTokens = refreshBitbucketToken(decryptedRefreshToken);
        
        // Update connection with new tokens
        connection.setAccessToken(encryptionService.encrypt(newTokens.accessToken()));
        if (newTokens.refreshToken() != null) {
            connection.setRefreshToken(encryptionService.encrypt(newTokens.refreshToken()));
        }
        connection.setTokenExpiresAt(newTokens.expiresAt());
        connection = connectionRepository.save(connection);
        
        log.info("Successfully refreshed Bitbucket access token for connection: {}", connection.getId());
        return connection;
    }
    
    /**
     * Refresh Bitbucket Connect App connection using JWT Bearer grant.
     * Connect Apps use shared secret to create JWT and exchange for access token.
     */
    private VcsConnection refreshBitbucketConnectAppConnection(VcsConnection connection, 
            BitbucketConnectInstallation installation) throws GeneralSecurityException, IOException {
        
        String sharedSecret = installation.getSharedSecret();
        if (sharedSecret == null || sharedSecret.isBlank()) {
            throw new VcsClientException("No shared secret available for Connect App installation: " + 
                    installation.getClientKey());
        }
        
        // Decrypt the shared secret
        String decryptedSecret;
        try {
            decryptedSecret = encryptionService.decrypt(sharedSecret);
        } catch (Exception e) {
            // Shared secret might be stored unencrypted (old installation) - use as-is
            log.warn("Could not decrypt shared secret, using as stored: {}", e.getMessage());
            decryptedSecret = sharedSecret;
        }
        
        // Create JWT for authentication
        long now = System.currentTimeMillis() / 1000;
        SecretKey key = Keys.hmacShaKeyFor(decryptedSecret.getBytes(StandardCharsets.UTF_8));
        
        String jwt = Jwts.builder()
                .setIssuer(installation.getClientKey())
                .setSubject(installation.getClientKey())
                .setIssuedAt(new java.util.Date(now * 1000))
                .setExpiration(new java.util.Date((now + 180) * 1000)) // 3 minutes validity
                .signWith(key)
                .compact();
        
        log.debug("Created JWT for token exchange, iss: {}", installation.getClientKey());
        
        // Exchange JWT for access token using JWT Bearer grant
        OkHttpClient httpClient = new OkHttpClient();
        Request request = new Request.Builder()
                .url(BITBUCKET_TOKEN_URL)
                .addHeader("Authorization", "JWT " + jwt)
                .addHeader("Content-Type", "application/x-www-form-urlencoded")
                .post(RequestBody.create("grant_type=urn:bitbucket:oauth2:jwt", FORM_MEDIA_TYPE))
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            String responseBody = response.body() != null ? response.body().string() : "";
            
            if (!response.isSuccessful()) {
                log.error("Failed to get access token via JWT Bearer: {} - {}", response.code(), responseBody);
                throw new IOException("Failed to refresh Connect App token: " + response.code() + " - " + responseBody);
            }
            
            JsonNode json = objectMapper.readTree(responseBody);
            String accessToken = json.path("access_token").asText();
            int expiresIn = json.path("expires_in").asInt(3600);
            LocalDateTime expiresAt = LocalDateTime.now().plusSeconds(expiresIn);
            
            // Update connection with new token
            connection.setAccessToken(encryptionService.encrypt(accessToken));
            connection.setTokenExpiresAt(expiresAt);
            connection = connectionRepository.save(connection);
            
            // Also update the installation's cached token
            installation.setAccessToken(encryptionService.encrypt(accessToken));
            installation.setTokenExpiresAt(expiresAt);
            connectInstallationRepository.save(installation);
            
            log.info("Successfully refreshed Bitbucket Connect App token for connection: {}", connection.getId());
            return connection;
        }
    }
    
    /**
     * Refresh GitHub App connection using installation access token.
     * GitHub App installation tokens expire after 1 hour.
     */
    private VcsConnection refreshGitHubAppConnection(VcsConnection connection) throws Exception {
        // The externalWorkspaceId stores the installation ID for GitHub App connections
        String installationIdStr = connection.getExternalWorkspaceId();
        if (installationIdStr == null || installationIdStr.isBlank()) {
            throw new VcsClientException("No installation ID found for GitHub App connection: " + connection.getId());
        }
        
        long installationId;
        try {
            installationId = Long.parseLong(installationIdStr);
        } catch (NumberFormatException e) {
            throw new VcsClientException("Invalid installation ID for connection: " + connection.getId());
        }
        
        // Use injected GitHub App credentials
        if (githubAppId == null || githubAppId.isBlank() || 
            githubAppPrivateKeyPath == null || githubAppPrivateKeyPath.isBlank()) {
            throw new VcsClientException("GitHub App credentials not configured for token refresh. " +
                    "Set codecrow.github.app.id and codecrow.github.app.private-key-path in application.properties");
        }
        
        // Create auth service and get new installation token
        org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService authService = 
                new org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService(githubAppId, githubAppPrivateKeyPath);
        var installationToken = authService.getInstallationAccessToken(installationId);
        
        // Update connection with new token
        connection.setAccessToken(encryptionService.encrypt(installationToken.token()));
        connection.setTokenExpiresAt(installationToken.expiresAt());
        connection = connectionRepository.save(connection);
        
        log.info("Successfully refreshed GitHub App installation token for connection: {}", connection.getId());
        return connection;
    }
    
    /**
     * Refresh GitLab OAuth connection using refresh token.
     */
    private VcsConnection refreshGitLabConnection(VcsConnection connection) 
            throws GeneralSecurityException, IOException {
        
        if (connection.getRefreshToken() == null) {
            throw new VcsClientException("No refresh token available for GitLab connection: " + connection.getId());
        }
        
        String decryptedRefreshToken = encryptionService.decrypt(connection.getRefreshToken());
        TokenResponse newTokens = refreshGitLabToken(decryptedRefreshToken);
        
        // Update connection with new tokens
        connection.setAccessToken(encryptionService.encrypt(newTokens.accessToken()));
        if (newTokens.refreshToken() != null) {
            connection.setRefreshToken(encryptionService.encrypt(newTokens.refreshToken()));
        }
        connection.setTokenExpiresAt(newTokens.expiresAt());
        connection = connectionRepository.save(connection);
        
        log.info("Successfully refreshed GitLab access token for connection: {}", connection.getId());
        return connection;
    }
    
    /**
     * Refresh GitLab access token using refresh token.
     */
    private TokenResponse refreshGitLabToken(String refreshToken) throws IOException {
        if (gitlabOAuthClientId == null || gitlabOAuthClientId.isBlank() ||
            gitlabOAuthClientSecret == null || gitlabOAuthClientSecret.isBlank()) {
            throw new IOException("GitLab OAuth credentials not configured. Set codecrow.gitlab.oauth.client-id and codecrow.gitlab.oauth.client-secret");
        }
        
        OkHttpClient httpClient = new OkHttpClient();
        
        // Determine GitLab token URL (support self-hosted)
        String tokenUrl = (gitlabBaseUrl != null && !gitlabBaseUrl.isBlank() && !gitlabBaseUrl.equals("https://gitlab.com"))
                ? gitlabBaseUrl.replaceAll("/$", "") + "/oauth/token"
                : GITLAB_TOKEN_URL;
        
        RequestBody body = new FormBody.Builder()
                .add("grant_type", "refresh_token")
                .add("refresh_token", refreshToken)
                .add("client_id", gitlabOAuthClientId)
                .add("client_secret", gitlabOAuthClientSecret)
                .build();
        
        Request request = new Request.Builder()
                .url(tokenUrl)
                .header("Accept", "application/json")
                .post(body)
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                throw new IOException("Failed to refresh GitLab token: " + response.code() + " - " + errorBody);
            }
            
            String responseBody = response.body().string();
            JsonNode json = objectMapper.readTree(responseBody);
            
            String accessToken = json.get("access_token").asText();
            String newRefreshToken = json.has("refresh_token") ? json.get("refresh_token").asText() : null;
            int expiresIn = json.has("expires_in") ? json.get("expires_in").asInt() : 7200;
            
            LocalDateTime expiresAt = LocalDateTime.now().plusSeconds(expiresIn);
            
            log.debug("GitLab token refreshed successfully. New token expires at: {}", expiresAt);
            
            return new TokenResponse(accessToken, newRefreshToken, expiresAt);
        }
    }
    
    /**
     * Refresh Bitbucket access token using refresh token.
     */
    private TokenResponse refreshBitbucketToken(String refreshToken) throws IOException {
        if (bitbucketAppClientId == null || bitbucketAppClientId.isBlank() ||
            bitbucketAppClientSecret == null || bitbucketAppClientSecret.isBlank()) {
            throw new IOException("Bitbucket App credentials not configured. Set codecrow.bitbucket.app.client-id and codecrow.bitbucket.app.client-secret");
        }
        
        OkHttpClient httpClient = new OkHttpClient();
        
        String credentials = Base64.getEncoder().encodeToString(
                (bitbucketAppClientId + ":" + bitbucketAppClientSecret).getBytes(StandardCharsets.UTF_8));
        
        RequestBody body = new FormBody.Builder()
                .add("grant_type", "refresh_token")
                .add("refresh_token", refreshToken)
                .build();
        
        Request request = new Request.Builder()
                .url(BITBUCKET_TOKEN_URL)
                .header("Authorization", "Basic " + credentials)
                .post(body)
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                throw new IOException("Failed to refresh token: " + response.code() + " - " + errorBody);
            }
            
            String responseBody = response.body().string();
            JsonNode json = objectMapper.readTree(responseBody);
            
            String accessToken = json.get("access_token").asText();
            String newRefreshToken = json.has("refresh_token") ? json.get("refresh_token").asText() : null;
            int expiresIn = json.has("expires_in") ? json.get("expires_in").asInt() : 7200;
            
            LocalDateTime expiresAt = LocalDateTime.now().plusSeconds(expiresIn);
            
            log.debug("Token refreshed successfully. New token expires at: {}", expiresAt);
            
            return new TokenResponse(accessToken, newRefreshToken, expiresAt);
        }
    }
    
    /**
     * Token response from OAuth provider.
     */
    private record TokenResponse(String accessToken, String refreshToken, LocalDateTime expiresAt) {}
    
    // ==================== Private Methods ====================
    
    private OkHttpClient createHttpClient(VcsConnection connection) throws GeneralSecurityException {
        EVcsConnectionType connectionType = connection.getConnectionType();
        
        // Handle null connectionType as OAUTH_MANUAL (legacy connections)
        if (connectionType == null) {
            log.warn("Connection {} has null connectionType, treating as OAUTH_MANUAL", connection.getId());
            connectionType = EVcsConnectionType.OAUTH_MANUAL;
        }
        
        log.info("createHttpClient: connection={}, connectionType={}, provider={}", 
                connection.getId(), connectionType, connection.getProviderType());
        
        return switch (connectionType) {
            case APP -> createAppHttpClient(connection);
            case OAUTH_MANUAL -> createOAuthManualHttpClient(connection);
            case PERSONAL_TOKEN, REPOSITORY_TOKEN -> createPersonalTokenHttpClient(connection);
            default -> throw new VcsClientException("Unsupported connection type: " + connectionType);
        };
    }
    
    /**
     * Create HTTP client for APP-type connections.
     * Uses OAuth2 access token (bearer token authentication).
     */
    private OkHttpClient createAppHttpClient(VcsConnection connection) throws GeneralSecurityException {
        String accessToken = connection.getAccessToken();
        if (accessToken == null || accessToken.isBlank()) {
            throw new VcsClientException("No access token found for APP connection: " + connection.getId());
        }
        
        // Decrypt the access token
        String decryptedToken = encryptionService.decrypt(accessToken);
        
        return httpClientFactory.createClientWithBearerToken(decryptedToken);
    }
    
    /**
     * Create HTTP client for OAUTH_MANUAL connections.
     * Uses OAuth Consumer key/secret (OAuth 1.0 style or OAuth 2.0 client credentials).
     */
    private OkHttpClient createOAuthManualHttpClient(VcsConnection connection) throws GeneralSecurityException {
        // For legacy connections, credentials are stored in the configuration JSON
        if (connection.getConfiguration() == null) {
            throw new VcsClientException("No configuration found for OAUTH_MANUAL connection: " + connection.getId());
        }
        
        if (connection.getConfiguration() instanceof BitbucketCloudConfig config) {
            String clientId = encryptionService.decrypt(config.oAuthKey());
            String clientSecret = encryptionService.decrypt(config.oAuthToken());
            
            return httpClientFactory.createClient(clientId, clientSecret, 
                    connection.getProviderType().getId());
        }
        
        throw new VcsClientException("Unsupported configuration type for connection: " + connection.getId());
    }
    
    /**
     * Create HTTP client for PERSONAL_TOKEN connections.
     * Uses personal access token (bearer token authentication).
     */
    private OkHttpClient createPersonalTokenHttpClient(VcsConnection connection) throws GeneralSecurityException {
        String accessToken;
        
        // Get access token - either from direct field or from configuration
        if (connection.getAccessToken() != null && !connection.getAccessToken().isBlank()) {
            accessToken = encryptionService.decrypt(connection.getAccessToken());
        } else if (connection.getConfiguration() != null) {
            // Extract token from config for GitHub/GitLab
            accessToken = extractTokenFromConfig(connection);
        } else {
            throw new VcsClientException("No access token found for PERSONAL_TOKEN connection: " + connection.getId());
        }
        
        // Use provider-specific client factory for proper headers
        return switch (connection.getProviderType()) {
            case GITHUB -> httpClientFactory.createGitHubClient(accessToken);
            case GITLAB -> httpClientFactory.createGitLabClient(accessToken);
            default -> httpClientFactory.createClientWithBearerToken(accessToken);
        };
    }
    
    private String extractTokenFromConfig(VcsConnection connection) throws GeneralSecurityException {
        if (connection.getConfiguration() instanceof org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig config) {
            return config.accessToken();  // GitHub config stores token in plain text
        }
        if (connection.getConfiguration() instanceof org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig config) {
            return config.accessToken();  // GitLab config stores token in plain text
        }
        throw new VcsClientException("Cannot extract token from config for connection: " + connection.getId());
    }
    
    /**
     * Create a VcsClient for the given provider with the authorized HTTP client.
     */
    private VcsClient createVcsClient(EVcsProvider provider, OkHttpClient httpClient) {
        return switch (provider) {
            case BITBUCKET_CLOUD -> new BitbucketCloudClient(httpClient);
            case GITHUB -> new GitHubClient(httpClient);
            case GITLAB -> new org.rostilos.codecrow.vcsclient.gitlab.GitLabClient(httpClient);
            default -> throw new VcsClientException("Unsupported provider: " + provider);
        };
    }
}
