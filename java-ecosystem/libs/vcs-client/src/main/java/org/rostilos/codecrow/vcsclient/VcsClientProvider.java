package org.rostilos.codecrow.vcsclient;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.time.LocalDateTime;
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
    
    private final VcsConnectionRepository connectionRepository;
    private final TokenEncryptionService encryptionService;
    private final HttpAuthorizedClientFactory httpClientFactory;
    
    public VcsClientProvider(
            VcsConnectionRepository connectionRepository,
            TokenEncryptionService encryptionService,
            HttpAuthorizedClientFactory httpClientFactory
    ) {
        this.connectionRepository = connectionRepository;
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
     * 
     * @param connection the VCS connection entity
     * @return authorized VcsClient
     * @throws VcsClientException if client creation fails
     */
    public VcsClient getClient(VcsConnection connection) {
        try {
            OkHttpClient httpClient = createHttpClient(connection);
            return createVcsClient(connection.getProviderType(), httpClient);
        } catch (GeneralSecurityException e) {
            throw new VcsClientException("Failed to decrypt credentials for connection: " + connection.getId(), e);
        }
    }
    
    /**
     * Get an authorized OkHttpClient for the given connection.
     * Use this for low-level HTTP operations.
     * 
     * @param connectionId the VCS connection ID
     * @return authorized OkHttpClient
     */
    public OkHttpClient getHttpClient(Long connectionId) {
        VcsConnection connection = connectionRepository.findById(connectionId)
                .orElseThrow(() -> new IllegalArgumentException("VCS connection not found: " + connectionId));
        try {
            return createHttpClient(connection);
        } catch (GeneralSecurityException e) {
            throw new VcsClientException("Failed to decrypt credentials for connection: " + connectionId, e);
        }
    }
    
    /**
     * Get an authorized OkHttpClient for the given connection within a workspace.
     * 
     * @param workspaceId the workspace ID
     * @param connectionId the VCS connection ID
     * @return authorized OkHttpClient
     */
    public OkHttpClient getHttpClient(Long workspaceId, Long connectionId) {
        VcsConnection connection = connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException(
                        "VCS connection not found: " + connectionId + " in workspace: " + workspaceId));
        try {
            return createHttpClient(connection);
        } catch (GeneralSecurityException e) {
            throw new VcsClientException("Failed to decrypt credentials for connection: " + connectionId, e);
        }
    }
    
    /**
     * Get an authorized OkHttpClient for the given connection entity.
     * Use this when you already have the connection entity.
     * 
     * @param connection the VCS connection entity
     * @return authorized OkHttpClient
     */
    public OkHttpClient getHttpClient(VcsConnection connection) {
        try {
            return createHttpClient(connection);
        } catch (GeneralSecurityException e) {
            throw new VcsClientException("Failed to decrypt credentials for connection: " + connection.getId(), e);
        }
    }
    
    /**
     * Check if a connection's token needs refresh.
     * 
     * @param connection the VCS connection
     * @return true if token is expired or will expire within 5 minutes
     */
    public boolean needsTokenRefresh(VcsConnection connection) {
        if (connection.getTokenExpiresAt() == null) {
            return false; // No expiration = doesn't need refresh (e.g., OAuth Consumer)
        }
        // Refresh if token expires within 5 minutes
        return connection.getTokenExpiresAt().isBefore(LocalDateTime.now().plusMinutes(5));
    }
    
    /**
     * Refresh the access token for a connection.
     * Only applicable for APP-type connections with refresh tokens.
     * 
     * @param connection the VCS connection
     * @return updated connection with new tokens
     * @throws VcsClientException if refresh fails
     */
    public VcsConnection refreshToken(VcsConnection connection) {
        if (connection.getConnectionType() != EVcsConnectionType.APP) {
            throw new VcsClientException("Token refresh only supported for APP connections");
        }
        if (connection.getRefreshToken() == null) {
            throw new VcsClientException("No refresh token available for connection: " + connection.getId());
        }
        
        // TODO: Implement token refresh for each provider
        // This would call the provider's token endpoint with the refresh token
        throw new UnsupportedOperationException("Token refresh not yet implemented");
    }
    
    // ==================== Private Methods ====================
    
    private OkHttpClient createHttpClient(VcsConnection connection) throws GeneralSecurityException {
        EVcsConnectionType connectionType = connection.getConnectionType();
        
        // Handle null connectionType as OAUTH_MANUAL (legacy connections)
        if (connectionType == null) {
            connectionType = EVcsConnectionType.OAUTH_MANUAL;
        }
        
        return switch (connectionType) {
            case APP -> createAppHttpClient(connection);
            case OAUTH_MANUAL -> createOAuthManualHttpClient(connection);
            case PERSONAL_TOKEN -> createPersonalTokenHttpClient(connection);
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
        String accessToken = connection.getAccessToken();
        if (accessToken == null || accessToken.isBlank()) {
            throw new VcsClientException("No access token found for PERSONAL_TOKEN connection: " + connection.getId());
        }
        
        String decryptedToken = encryptionService.decrypt(accessToken);
        return httpClientFactory.createClientWithBearerToken(decryptedToken);
    }
    
    /**
     * Create a VcsClient for the given provider with the authorized HTTP client.
     */
    private VcsClient createVcsClient(EVcsProvider provider, OkHttpClient httpClient) {
        return switch (provider) {
            case BITBUCKET_CLOUD -> new BitbucketCloudClient(httpClient);
            // TODO: Add other providers
            // case GITHUB -> new GitHubClient(httpClient);
            // case GITLAB -> new GitLabClient(httpClient);
            default -> throw new VcsClientException("Unsupported provider: " + provider);
        };
    }
}
