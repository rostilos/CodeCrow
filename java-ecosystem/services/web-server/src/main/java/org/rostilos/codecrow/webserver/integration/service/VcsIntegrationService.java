package org.rostilos.codecrow.webserver.integration.service;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.ai.AiConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.BitbucketConnectInstallationRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.HttpAuthorizedClientFactory;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientFactory;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsRepository;
import org.rostilos.codecrow.vcsclient.model.VcsRepositoryPage;
import org.rostilos.codecrow.vcsclient.model.VcsWorkspace;
import org.rostilos.codecrow.webserver.integration.dto.request.RepoOnboardRequest;
import org.rostilos.codecrow.webserver.exception.IntegrationException;
import org.rostilos.codecrow.webserver.integration.dto.response.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.util.*;
import java.util.stream.Collectors;

import org.rostilos.codecrow.webserver.admin.service.ISiteSettingsProvider;

/**
 * Service for VCS provider integrations.
 * Handles app installation, OAuth callbacks, repository listing, and onboarding.
 */
@Service
public class VcsIntegrationService {
    
    private static final Logger log = LoggerFactory.getLogger(VcsIntegrationService.class);
    
    // Bitbucket OAuth App events for webhooks
    private static final List<String> BITBUCKET_WEBHOOK_EVENTS = List.of(
        "pullrequest:created",
        "pullrequest:updated",
        "pullrequest:fulfilled",
        "pullrequest:rejected",
        "pullrequest:comment_created",
        "repo:push"
    );
    
    // GitHub webhook events
    private static final List<String> GITHUB_WEBHOOK_EVENTS = List.of(
        "pull_request",
        "push",
        "pull_request_review",
        "pull_request_review_comment",
        "discussion_comment",
        "issue_comment"
    );
    
    // GitLab webhook events (mapped from generic events)
    private static final List<String> GITLAB_WEBHOOK_EVENTS = List.of(
        "merge_requests_events",   // MR created/updated
        "note_events",             // Comments on MRs
        "push_events"              // Push to branch
    );
    
    private final VcsConnectionRepository connectionRepository;
    private final VcsRepoBindingRepository bindingRepository;
    private final WorkspaceRepository workspaceRepository;
    private final ProjectRepository projectRepository;
    private final AiConnectionRepository aiConnectionRepository;
    private final BitbucketConnectInstallationRepository connectInstallationRepository;
    private final TokenEncryptionService encryptionService;
    private final HttpAuthorizedClientFactory httpClientFactory;
    private final VcsClientFactory vcsClientFactory;
    private final VcsClientProvider vcsClientProvider;
    private final OAuthStateService oAuthStateService;
    private final ISiteSettingsProvider siteSettingsProvider;
    
    // Legacy GitHub OAuth (for backward compatibility, cloud only)
    @Value("${codecrow.github.oauth.client-id:}")
    private String githubOAuthClientId;
    
    @Value("${codecrow.github.oauth.client-secret:}")
    private String githubOAuthClientSecret;
    
    public VcsIntegrationService(
            VcsConnectionRepository connectionRepository,
            VcsRepoBindingRepository bindingRepository,
            WorkspaceRepository workspaceRepository,
            ProjectRepository projectRepository,
            AiConnectionRepository aiConnectionRepository,
            BitbucketConnectInstallationRepository connectInstallationRepository,
            TokenEncryptionService encryptionService,
            HttpAuthorizedClientFactory httpClientFactory,
            VcsClientProvider vcsClientProvider,
            OAuthStateService oAuthStateService,
            ISiteSettingsProvider siteSettingsProvider
    ) {
        this.connectionRepository = connectionRepository;
        this.bindingRepository = bindingRepository;
        this.workspaceRepository = workspaceRepository;
        this.projectRepository = projectRepository;
        this.aiConnectionRepository = aiConnectionRepository;
        this.connectInstallationRepository = connectInstallationRepository;
        this.encryptionService = encryptionService;
        this.httpClientFactory = httpClientFactory;
        this.vcsClientFactory = new VcsClientFactory(httpClientFactory);
        this.vcsClientProvider = vcsClientProvider;
        this.oAuthStateService = oAuthStateService;
        this.siteSettingsProvider = siteSettingsProvider;
    }
    
    /**
     * Get the installation URL for a VCS provider app.
     */
    public InstallUrlResponse getInstallUrl(EVcsProvider provider, Long workspaceId) {
        validateProviderSupported(provider);
        
        return switch (provider) {
            case BITBUCKET_CLOUD -> getBitbucketCloudInstallUrl(workspaceId, null);
            case GITHUB -> getGitHubInstallUrl(workspaceId, null);
            case GITLAB -> getGitLabInstallUrl(workspaceId, null);
            default -> throw new IntegrationException("Provider " + provider + " does not support app installation");
        };
    }
    
    /**
     * Get the reconnection URL for an existing VCS connection.
     * This initiates the OAuth flow to refresh tokens for an expired/invalid connection.
     */
    public InstallUrlResponse getReconnectUrl(Long workspaceId, Long connectionId) {
        VcsConnection connection = connectionRepository.findById(connectionId)
                .orElseThrow(() -> new IntegrationException("Connection not found: " + connectionId));
        
        // Verify connection belongs to this workspace
        if (!connection.getWorkspace().getId().equals(workspaceId)) {
            throw new IntegrationException("Connection does not belong to this workspace");
        }
        
        EVcsProvider provider = connection.getProviderType();
        validateProviderSupported(provider);
        
        log.info("Generating reconnect URL for connection {} (provider: {})", connectionId, provider);
        
        return switch (provider) {
            case BITBUCKET_CLOUD -> getBitbucketCloudInstallUrl(workspaceId, connectionId);
            case GITHUB -> getGitHubInstallUrl(workspaceId, connectionId);
            case GITLAB -> getGitLabInstallUrl(workspaceId, connectionId);
            default -> throw new IntegrationException("Provider " + provider + " does not support reconnection");
        };
    }
    
    private InstallUrlResponse getBitbucketCloudInstallUrl(Long workspaceId, Long connectionId) {
        var bbSettings = siteSettingsProvider.getBitbucketSettings();
        String bbClientId = bbSettings.clientId();
        String bbClientSecret = bbSettings.clientSecret();
        if (bbClientId == null || bbClientId.isBlank()) {
            throw new IntegrationException(
                "Bitbucket OAuth App is not configured. " +
                "Please configure Bitbucket settings in Site Admin."
            );
        }
        
        if (bbClientSecret == null || bbClientSecret.isBlank()) {
            throw new IntegrationException(
                "Bitbucket OAuth App client secret is not configured. " +
                "Please configure Bitbucket settings in Site Admin."
            );
        }
        
        String state = generateState(EVcsProvider.BITBUCKET_CLOUD, workspaceId, connectionId);
        String callbackUrl = siteSettingsProvider.getBaseUrlSettings().baseUrl() + "/api/integrations/bitbucket-cloud/app/callback";
        
        log.info("Generated Bitbucket install URL with callback: {} (reconnect: {})", callbackUrl, connectionId != null);
        
        String installUrl = "https://bitbucket.org/site/oauth2/authorize" +
                "?client_id=" + URLEncoder.encode(bbClientId, StandardCharsets.UTF_8) +
                "&response_type=code" +
                "&redirect_uri=" + URLEncoder.encode(callbackUrl, StandardCharsets.UTF_8) +
                "&state=" + URLEncoder.encode(state, StandardCharsets.UTF_8);
        
        return new InstallUrlResponse(installUrl, EVcsProvider.BITBUCKET_CLOUD.getId(), state);
    }
    
    private InstallUrlResponse getGitHubInstallUrl(Long workspaceId, Long connectionId) {
        // Prefer GitHub App installation flow (for private repo access)
        String githubSlug = siteSettingsProvider.getGitHubSettings().slug();
        if (githubSlug != null && !githubSlug.isBlank()) {
            String state = generateState(EVcsProvider.GITHUB, workspaceId, connectionId);
            
            // GitHub App installation URL
            // When user clicks this, they'll be taken to GitHub to install the app
            // After installation, GitHub redirects to the callback URL with installation_id
            String installUrl = "https://github.com/apps/" + githubSlug + "/installations/new" +
                    "?state=" + URLEncoder.encode(state, StandardCharsets.UTF_8);
            
            log.info("Generated GitHub App install URL for app: {}", githubSlug);
            
            return new InstallUrlResponse(installUrl, EVcsProvider.GITHUB.getId(), state);
        }
        
        // Fallback to OAuth flow (limited to public repos unless user grants repo scope)
        if (githubOAuthClientId == null || githubOAuthClientId.isBlank()) {
            throw new IntegrationException(
                "GitHub App is not configured. " +
                "Please configure the GitHub App Slug in Site Admin settings, " +
                "or set 'codecrow.github.oauth.client-id' for OAuth flow."
            );
        }
        
        if (githubOAuthClientSecret == null || githubOAuthClientSecret.isBlank()) {
            throw new IntegrationException(
                "GitHub OAuth client secret is not configured. " +
                "Please set 'codecrow.github.oauth.client-secret' in your application.properties."
            );
        }
        
        String state = generateState(EVcsProvider.GITHUB, workspaceId, connectionId);
        String callbackUrl = siteSettingsProvider.getBaseUrlSettings().baseUrl() + "/api/integrations/github/app/callback";
        
        log.info("Generated GitHub OAuth URL with callback: {} (reconnect: {})", callbackUrl, connectionId != null);
        
        // Request repo and user scopes for full repository access (space-separated for GitHub)
        String scope = "repo read:user read:org";
        
        String installUrl = "https://github.com/login/oauth/authorize" +
                "?client_id=" + URLEncoder.encode(githubOAuthClientId, StandardCharsets.UTF_8) +
                "&scope=" + URLEncoder.encode(scope, StandardCharsets.UTF_8) +
                "&redirect_uri=" + URLEncoder.encode(callbackUrl, StandardCharsets.UTF_8) +
                "&state=" + URLEncoder.encode(state, StandardCharsets.UTF_8);
        
        return new InstallUrlResponse(installUrl, EVcsProvider.GITHUB.getId(), state);
    }
    
    /**
     * Get the GitLab OAuth installation URL.
     * Supports both GitLab.com and self-hosted GitLab instances.
     */
    private InstallUrlResponse getGitLabInstallUrl(Long workspaceId, Long connectionId) {
        var glSettings = siteSettingsProvider.getGitLabSettings();
        String glClientId = glSettings.clientId();
        String glClientSecret = glSettings.clientSecret();
        String glBaseUrl = glSettings.baseUrl();
        if (glClientId == null || glClientId.isBlank()) {
            throw new IntegrationException(
                "GitLab OAuth Application is not configured. " +
                "Please configure GitLab settings in Site Admin."
            );
        }
        
        if (glClientSecret == null || glClientSecret.isBlank()) {
            throw new IntegrationException(
                "GitLab OAuth Application secret is not configured. " +
                "Please configure GitLab settings in Site Admin."
            );
        }
        
        String state = generateState(EVcsProvider.GITLAB, workspaceId, connectionId);
        String callbackUrl = siteSettingsProvider.getBaseUrlSettings().baseUrl() + "/api/integrations/gitlab/app/callback";
        
        // Determine GitLab base URL (gitlab.com or self-hosted)
        String gitlabHost = (glBaseUrl != null && !glBaseUrl.isBlank()) 
                ? glBaseUrl.replaceAll("/$", "")  // Remove trailing slash
                : "https://gitlab.com";
        
        log.info("Generated GitLab OAuth URL with callback: {} (host: {}, reconnect: {})", callbackUrl, gitlabHost, connectionId != null);
        
        // GitLab OAuth scopes (space-separated)
        String scope = "api read_user read_repository write_repository";
        
        String installUrl = gitlabHost + "/oauth/authorize" +
                "?client_id=" + URLEncoder.encode(glClientId, StandardCharsets.UTF_8) +
                "&redirect_uri=" + URLEncoder.encode(callbackUrl, StandardCharsets.UTF_8) +
                "&response_type=code" +
                "&scope=" + URLEncoder.encode(scope, StandardCharsets.UTF_8) +
                "&state=" + URLEncoder.encode(state, StandardCharsets.UTF_8);
        
        return new InstallUrlResponse(installUrl, EVcsProvider.GITLAB.getId(), state);
    }
    
    /**
     * Handle OAuth callback from a VCS provider app.
     * Supports both new connections and reconnection of existing connections.
     */
    @Transactional
    public VcsConnectionDTO handleAppCallback(EVcsProvider provider, String code, String state, Long workspaceId)
            throws GeneralSecurityException, IOException {
        validateProviderSupported(provider);
        
        // Extract connectionId from state if this is a reconnection
        OAuthStateService.OAuthStateData stateData = oAuthStateService.validateAndExtractState(state);
        Long connectionId = stateData != null ? stateData.connectionId() : null;
        
        if (connectionId != null) {
            log.info("Processing reconnection callback for connection {} (provider: {})", connectionId, provider);
        }
        
        return switch (provider) {
            case BITBUCKET_CLOUD -> handleBitbucketCloudCallback(code, state, workspaceId, connectionId);
            case GITHUB -> handleGitHubCallback(code, state, workspaceId, connectionId);
            case GITLAB -> handleGitLabCallback(code, state, workspaceId, connectionId);
            default -> throw new IntegrationException("Provider " + provider + " does not support app callback");
        };
    }
    
    /**
     * Handle GitHub App installation callback.
     * This is called when a user installs the GitHub App on their account/organization.
     * 
     * @param installationId the GitHub App installation ID
     * @param workspaceId the CodeCrow workspace ID
     * @return the created or updated VCS connection
     */
    @Transactional
    public VcsConnectionDTO handleGitHubAppInstallation(Long installationId, Long workspaceId) 
            throws GeneralSecurityException, IOException {
        
        var ghSettings = siteSettingsProvider.getGitHubSettings();
        String ghAppId = ghSettings.appId();
        String ghPrivateKeyPath = ghSettings.privateKeyPath();
        if (ghAppId == null || ghAppId.isBlank() || 
            ghPrivateKeyPath == null || ghPrivateKeyPath.isBlank()) {
            throw new IntegrationException(
                "GitHub App is not configured. Please configure GitHub settings in Site Admin."
            );
        }
        
        try {
            org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService authService =
                    new org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService(ghAppId, ghPrivateKeyPath);
            
            var installationInfo = authService.getInstallation(installationId);
            log.info("GitHub App installed on {}: {} ({})", 
                    installationInfo.accountType(), 
                    installationInfo.accountLogin(),
                    installationInfo.installationId());
            
            var installationToken = authService.getInstallationAccessToken(installationId);
            
            Workspace workspace = workspaceRepository.findById(workspaceId)
                    .orElseThrow(() -> new IntegrationException("Workspace not found"));
            
            List<VcsConnection> existingConnections = connectionRepository
                    .findByWorkspace_IdAndProviderType(workspaceId, EVcsProvider.GITHUB);
            
            VcsConnection connection = existingConnections.stream()
                    .filter(c -> c.getConnectionType() == EVcsConnectionType.APP)
                    .filter(c -> String.valueOf(installationId).equals(c.getExternalWorkspaceId()))
                    .findFirst()
                    .orElse(null);
            
            if (connection != null) {
                log.info("Updating existing GitHub App connection {} for installation {}", 
                        connection.getId(), installationId);
            } else {
                connection = new VcsConnection();
                connection.setWorkspace(workspace);
                connection.setProviderType(EVcsProvider.GITHUB);
                connection.setConnectionType(EVcsConnectionType.APP);
            }
            
            connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
            connection.setAccessToken(encryptionService.encrypt(installationToken.token()));
            connection.setTokenExpiresAt(installationToken.expiresAt());
            connection.setExternalWorkspaceId(String.valueOf(installationId));
            connection.setExternalWorkspaceSlug(installationInfo.accountLogin());
            connection.setConnectionName("GitHub – " + installationInfo.accountLogin());
            
            // Store installation ID for token refresh
            String configJson = String.format(
                "{\"installationId\":%d,\"accountType\":\"%s\",\"accountLogin\":\"%s\"}",
                installationId, installationInfo.accountType(), installationInfo.accountLogin()
            );
            // TODO: we may need to add a configuration field to VcsConnection
            // TODO: For now, we'll store the installation ID in the externalWorkspaceId field
            
            VcsClient client = vcsClientFactory.createClient(EVcsProvider.GITHUB, installationToken.token(), null);
            int repoCount = client.getRepositoryCount(installationInfo.accountLogin());
            connection.setRepoCount(repoCount);
            
            VcsConnection saved = connectionRepository.save(connection);
            log.info("Saved GitHub App connection {} for workspace {} (installation: {})", 
                    saved.getId(), workspaceId, installationId);
            
            return VcsConnectionDTO.fromEntity(saved);
            
        } catch (Exception e) {
            log.error("Failed to handle GitHub App installation: {}", e.getMessage(), e);
            throw new IntegrationException("Failed to handle GitHub App installation: " + e.getMessage());
        }
    }

    private VcsConnectionDTO handleBitbucketCloudCallback(String code, String state, Long workspaceId, Long connectionId) 
            throws GeneralSecurityException, IOException {
        
        // Exchange code for tokens
        TokenResponse tokens = exchangeBitbucketCode(code);
        
        // Create VCS client with the new tokens
        VcsClient client = vcsClientFactory.createClient(EVcsProvider.BITBUCKET_CLOUD, tokens.accessToken, tokens.refreshToken);
        
        // Get workspace info from Bitbucket
        List<VcsWorkspace> workspaces = client.listWorkspaces();
        VcsWorkspace bbWorkspace = workspaces.isEmpty() ? null : workspaces.get(0);
        
        // Get CodeCrow workspace
        Workspace workspace = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new IntegrationException("Workspace not found"));
        
        // If reconnecting, use the specified connection
        VcsConnection connection = null;
        if (connectionId != null) {
            connection = connectionRepository.findById(connectionId)
                    .orElseThrow(() -> new IntegrationException("Connection not found for reconnection: " + connectionId));
            log.info("Reconnecting existing Bitbucket Cloud connection {} for workspace {}", connectionId, workspaceId);
        } else if (bbWorkspace != null) {
            // Look for existing APP connection for this Bitbucket workspace
            List<VcsConnection> existingConnections = connectionRepository
                    .findByWorkspace_IdAndProviderType(workspaceId, EVcsProvider.BITBUCKET_CLOUD);
            
            connection = existingConnections.stream()
                    .filter(c -> c.getConnectionType() == EVcsConnectionType.APP)
                    .filter(c -> bbWorkspace.slug().equals(c.getExternalWorkspaceSlug()) || 
                                 bbWorkspace.id().equals(c.getExternalWorkspaceId()))
                    .findFirst()
                    .orElse(null);
            
            if (connection != null) {
                log.info("Updating existing Bitbucket OAuth App connection {} for workspace {}", 
                        connection.getId(), workspaceId);
            }
        }
        
        // Create new connection if none exists
        if (connection == null) {
            connection = new VcsConnection();
            connection.setWorkspace(workspace);
            connection.setProviderType(EVcsProvider.BITBUCKET_CLOUD);
            connection.setConnectionType(EVcsConnectionType.APP);
        }
        
        // Update connection with new tokens
        connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
        connection.setAccessToken(encryptionService.encrypt(tokens.accessToken));
        connection.setRefreshToken(tokens.refreshToken != null ? encryptionService.encrypt(tokens.refreshToken) : null);
        connection.setTokenExpiresAt(tokens.expiresAt);
        connection.setScopes(tokens.scopes);
        
        if (bbWorkspace != null) {
            connection.setExternalWorkspaceId(bbWorkspace.id());
            connection.setExternalWorkspaceSlug(bbWorkspace.slug());
            connection.setConnectionName("Bitbucket Cloud – " + bbWorkspace.name());
            
            // Get repo count
            int repoCount = client.getRepositoryCount(bbWorkspace.slug());
            connection.setRepoCount(repoCount);
        } else {
            connection.setConnectionName("Bitbucket OAuth App");
        }
        
        VcsConnection saved = connectionRepository.save(connection);
        log.info("Saved Bitbucket OAuth App connection {} for workspace {}", saved.getId(), workspaceId);
        
        return VcsConnectionDTO.fromEntity(saved);
    }
    
    private TokenResponse exchangeBitbucketCode(String code) throws IOException {
        // Use OkHttp to exchange code for tokens
        okhttp3.OkHttpClient httpClient = new okhttp3.OkHttpClient();
        
        var bbSettings = siteSettingsProvider.getBitbucketSettings();
        String credentials = Base64.getEncoder().encodeToString(
                (bbSettings.clientId() + ":" + bbSettings.clientSecret()).getBytes(StandardCharsets.UTF_8));
        
        String callbackUrl = siteSettingsProvider.getBaseUrlSettings().baseUrl() + "/api/integrations/bitbucket-cloud/app/callback";
        
        okhttp3.RequestBody body = new okhttp3.FormBody.Builder()
                .add("grant_type", "authorization_code")
                .add("code", code)
                .add("redirect_uri", callbackUrl)
                .build();
        
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url("https://bitbucket.org/site/oauth2/access_token")
                .header("Authorization", "Basic " + credentials)
                .post(body)
                .build();
        
        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                throw new IOException("Failed to exchange code: " + response.code() + " - " + errorBody);
            }
            
            String responseBody = response.body().string();
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            com.fasterxml.jackson.databind.JsonNode json = mapper.readTree(responseBody);
            
            String accessToken = json.get("access_token").asText();
            String refreshToken = json.has("refresh_token") ? json.get("refresh_token").asText() : null;
            int expiresIn = json.has("expires_in") ? json.get("expires_in").asInt() : 7200;
            String scopes = json.has("scopes") ? json.get("scopes").asText() : null;
            
            LocalDateTime expiresAt = LocalDateTime.now().plusSeconds(expiresIn);
            
            return new TokenResponse(accessToken, refreshToken, expiresAt, scopes);
        }
    }
    
    private VcsConnectionDTO handleGitHubCallback(String code, String state, Long workspaceId, Long connectionId) 
            throws GeneralSecurityException, IOException {
        
        TokenResponse tokens = exchangeGitHubCode(code);
        
        VcsClient client = vcsClientFactory.createClient(EVcsProvider.GITHUB, tokens.accessToken, tokens.refreshToken);
        
        var currentUser = client.getCurrentUser();
        String username = currentUser != null ? currentUser.username() : null;
        
        Workspace workspace = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new IntegrationException("Workspace not found"));
        
        // If reconnecting, use the specified connection
        VcsConnection connection = null;
        if (connectionId != null) {
            connection = connectionRepository.findById(connectionId)
                    .orElseThrow(() -> new IntegrationException("Connection not found for reconnection: " + connectionId));
            log.info("Reconnecting existing GitHub connection {} for workspace {}", connectionId, workspaceId);
        } else if (username != null) {
            List<VcsConnection> existingConnections = connectionRepository
                    .findByWorkspace_IdAndProviderType(workspaceId, EVcsProvider.GITHUB);
            
            connection = existingConnections.stream()
                    .filter(c -> c.getConnectionType() == EVcsConnectionType.APP)
                    .filter(c -> username.equals(c.getExternalWorkspaceSlug()))
                    .findFirst()
                    .orElse(null);
            
            if (connection != null) {
                log.info("Updating existing GitHub App connection {} for workspace {}", 
                        connection.getId(), workspaceId);
            }
        }
        
        if (connection == null) {
            connection = new VcsConnection();
            connection.setWorkspace(workspace);
            connection.setProviderType(EVcsProvider.GITHUB);
            connection.setConnectionType(EVcsConnectionType.APP);
        }
        
        connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
        connection.setAccessToken(encryptionService.encrypt(tokens.accessToken));
        connection.setRefreshToken(tokens.refreshToken != null ? encryptionService.encrypt(tokens.refreshToken) : null);
        connection.setTokenExpiresAt(tokens.expiresAt);
        connection.setScopes(tokens.scopes);
        
        if (username != null) {
            connection.setExternalWorkspaceId(username);
            connection.setExternalWorkspaceSlug(username);
            connection.setConnectionName("GitHub – " + username);
            
            int repoCount = client.getRepositoryCount(username);
            connection.setRepoCount(repoCount);
        } else {
            connection.setConnectionName("GitHub App");
        }
        
        VcsConnection saved = connectionRepository.save(connection);
        log.info("Saved GitHub App connection {} for workspace {}", saved.getId(), workspaceId);
        
        return VcsConnectionDTO.fromEntity(saved);
    }
    
    private TokenResponse exchangeGitHubCode(String code) throws IOException {
        okhttp3.OkHttpClient httpClient = new okhttp3.OkHttpClient();
        
        String callbackUrl = siteSettingsProvider.getBaseUrlSettings().baseUrl() + "/api/integrations/github/app/callback";
        
        okhttp3.RequestBody body = new okhttp3.FormBody.Builder()
                .add("client_id", githubOAuthClientId)
                .add("client_secret", githubOAuthClientSecret)
                .add("code", code)
                .add("redirect_uri", callbackUrl)
                .build();
        
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url("https://github.com/login/oauth/access_token")
                .header("Accept", "application/json")
                .post(body)
                .build();
        
        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                throw new IOException("Failed to exchange code: " + response.code() + " - " + errorBody);
            }
            
            String responseBody = response.body().string();
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            com.fasterxml.jackson.databind.JsonNode json = mapper.readTree(responseBody);
            
            if (json.has("error")) {
                throw new IOException("GitHub OAuth error: " + json.get("error").asText() + 
                        " - " + json.path("error_description").asText(""));
            }
            
            String accessToken = json.get("access_token").asText();
            // GitHub OAuth tokens don't have refresh tokens or expiry by default
            String scopes = json.has("scope") ? json.get("scope").asText() : null;
            
            return new TokenResponse(accessToken, null, null, scopes);
        }
    }
    
    /**
     * Handle GitLab OAuth callback.
     * Exchanges the authorization code for tokens and creates/updates the VCS connection.
     */
    private VcsConnectionDTO handleGitLabCallback(String code, String state, Long workspaceId, Long connectionId) 
            throws GeneralSecurityException, IOException {
        
        TokenResponse tokens = exchangeGitLabCode(code);
        
        VcsClient client = vcsClientFactory.createClient(EVcsProvider.GITLAB, tokens.accessToken, tokens.refreshToken);
        
        // Get current user info from GitLab
        var currentUser = client.getCurrentUser();
        String username = currentUser != null ? currentUser.username() : null;
        
        Workspace workspace = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new IntegrationException("Workspace not found"));
        
        // If reconnecting, use the specified connection
        VcsConnection connection = null;
        if (connectionId != null) {
            connection = connectionRepository.findById(connectionId)
                    .orElseThrow(() -> new IntegrationException("Connection not found for reconnection: " + connectionId));
            log.info("Reconnecting existing GitLab connection {} for workspace {}", connectionId, workspaceId);
        } else if (username != null) {
            List<VcsConnection> existingConnections = connectionRepository
                    .findByWorkspace_IdAndProviderType(workspaceId, EVcsProvider.GITLAB);
            
            connection = existingConnections.stream()
                    .filter(c -> c.getConnectionType() == EVcsConnectionType.APP)
                    .filter(c -> username.equals(c.getExternalWorkspaceSlug()))
                    .findFirst()
                    .orElse(null);
            
            if (connection != null) {
                log.info("Updating existing GitLab OAuth connection {} for workspace {}", 
                        connection.getId(), workspaceId);
            }
        }
        
        // Create new connection if none exists
        if (connection == null) {
            connection = new VcsConnection();
            connection.setWorkspace(workspace);
            connection.setProviderType(EVcsProvider.GITLAB);
            connection.setConnectionType(EVcsConnectionType.APP);  // OAuth connection type
        }
        
        // Update connection with new tokens (encrypted at rest)
        connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
        connection.setAccessToken(encryptionService.encrypt(tokens.accessToken));
        connection.setRefreshToken(tokens.refreshToken != null ? encryptionService.encrypt(tokens.refreshToken) : null);
        connection.setTokenExpiresAt(tokens.expiresAt);
        connection.setScopes(tokens.scopes);
        
        // Set the GitLab base URL in the configuration for self-hosted instances
        var glSettingsForHost = siteSettingsProvider.getGitLabSettings();
        String gitlabHost = (glSettingsForHost.baseUrl() != null && !glSettingsForHost.baseUrl().isBlank()) 
                ? glSettingsForHost.baseUrl().replaceAll("/$", "")
                : "https://gitlab.com";
        
        // Store GitLab-specific configuration
        connection.setConfiguration(new org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig(
                null,  // accessToken is stored separately (encrypted)
                username,  // groupId = username for personal projects
                null,  // allowedRepos
                gitlabHost  // baseUrl for self-hosted instances
        ));
        
        if (username != null) {
            connection.setExternalWorkspaceId(username);
            connection.setExternalWorkspaceSlug(username);
            connection.setConnectionName("GitLab – " + username);
            
            // Get repository count
            try {
                int repoCount = client.getRepositoryCount(username);
                connection.setRepoCount(repoCount);
            } catch (Exception e) {
                log.warn("Could not fetch repository count for GitLab user {}: {}", username, e.getMessage());
                connection.setRepoCount(0);
            }
        } else {
            connection.setConnectionName("GitLab OAuth");
        }
        
        VcsConnection saved = connectionRepository.save(connection);
        log.info("Saved GitLab OAuth connection {} for workspace {} (user: {})", 
                saved.getId(), workspaceId, username);
        
        return VcsConnectionDTO.fromEntity(saved);
    }
    
    /**
     * Exchange GitLab authorization code for access tokens.
     * Follows OAuth 2.0 spec with proper error handling.
     */
    private TokenResponse exchangeGitLabCode(String code) throws IOException {
        okhttp3.OkHttpClient httpClient = new okhttp3.OkHttpClient();
        
        String callbackUrl = siteSettingsProvider.getBaseUrlSettings().baseUrl() + "/api/integrations/gitlab/app/callback";
        
        // Determine GitLab base URL
        var glExchSettings = siteSettingsProvider.getGitLabSettings();
        String gitlabHost = (glExchSettings.baseUrl() != null && !glExchSettings.baseUrl().isBlank()) 
                ? glExchSettings.baseUrl().replaceAll("/$", "")
                : "https://gitlab.com";
        
        // GitLab token exchange - POST with form body
        okhttp3.RequestBody body = new okhttp3.FormBody.Builder()
                .add("client_id", glExchSettings.clientId())
                .add("client_secret", glExchSettings.clientSecret())
                .add("code", code)
                .add("grant_type", "authorization_code")
                .add("redirect_uri", callbackUrl)
                .build();
        
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(gitlabHost + "/oauth/token")
                .header("Accept", "application/json")
                .post(body)
                .build();
        
        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            String responseBody = response.body() != null ? response.body().string() : "";
            
            if (!response.isSuccessful()) {
                log.error("GitLab token exchange failed: {} - {}", response.code(), responseBody);
                throw new IOException("Failed to exchange GitLab code: " + response.code() + " - " + responseBody);
            }
            
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            com.fasterxml.jackson.databind.JsonNode json = mapper.readTree(responseBody);
            
            if (json.has("error")) {
                String error = json.get("error").asText();
                String errorDesc = json.path("error_description").asText("");
                log.error("GitLab OAuth error: {} - {}", error, errorDesc);
                throw new IOException("GitLab OAuth error: " + error + " - " + errorDesc);
            }
            
            String accessToken = json.get("access_token").asText();
            String refreshToken = json.has("refresh_token") ? json.get("refresh_token").asText() : null;
            
            // GitLab tokens typically expire in 2 hours (7200 seconds)
            int expiresIn = json.has("expires_in") ? json.get("expires_in").asInt() : 7200;
            LocalDateTime expiresAt = LocalDateTime.now().plusSeconds(expiresIn);
            
            // GitLab returns scope (singular), not scopes
            String scopes = json.has("scope") ? json.get("scope").asText() : 
                           (json.has("scopes") ? json.get("scopes").asText() : null);
            
            log.info("GitLab token exchange successful. Token expires at: {}, scopes: {}", expiresAt, scopes);
            
            return new TokenResponse(accessToken, refreshToken, expiresAt, scopes);
        }
    }
    
    /**
     * List repositories from a VCS connection.
     * For REPOSITORY_TOKEN connections, returns only the single repository the token has access to.
     */
    public VcsRepositoryListDTO listRepositories(Long workspaceId, Long connectionId, String query, int page)
            throws IOException {
        
        VcsConnection connection = getConnection(workspaceId, connectionId);
        VcsClient client = createClientForConnection(connection);
        
        // For REPOSITORY_TOKEN connections, we can only access the single repository
        // Return that repository directly without listing
        if (connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN) {
            String repoPath = connection.getRepositoryPath();
            if (repoPath != null && !repoPath.isBlank()) {
                // Fetch the single repository
                VcsRepository repo = client.getRepository("", repoPath);
                if (repo == null) {
                    // Return empty list if repo not found
                    return new VcsRepositoryListDTO(List.of(), 1, 1, 0, 0, false, false);
                }
                
                boolean isOnboarded = bindingRepository.existsByProviderAndExternalRepoId(
                        connection.getProviderType(), repo.id());
                
                List<VcsRepositoryListDTO.VcsRepositoryDTO> items = List.of(
                        VcsRepositoryListDTO.VcsRepositoryDTO.fromModel(repo, isOnboarded)
                );
                
                return new VcsRepositoryListDTO(items, 1, 1, 1, 1, false, false);
            }
        }
        
        String externalWorkspaceId = getExternalWorkspaceId(connection);
        
        VcsRepositoryPage repoPage;
        if (query != null && !query.isBlank()) {
            repoPage = client.searchRepositories(externalWorkspaceId, query, page);
        } else {
            repoPage = client.listRepositories(externalWorkspaceId, page);
        }
        
        // Get already onboarded repo IDs
        Set<String> onboardedRepoIds = bindingRepository
                .findByVcsConnection_Id(connectionId)
                .stream()
                .map(VcsRepoBinding::getExternalRepoId)
                .collect(Collectors.toSet());
        
        List<VcsRepositoryListDTO.VcsRepositoryDTO> items = repoPage.items().stream()
                .map(repo -> VcsRepositoryListDTO.VcsRepositoryDTO.fromModel(repo, onboardedRepoIds.contains(repo.id())))
                .toList();
        
        return new VcsRepositoryListDTO(
                items,
                repoPage.page(),
                repoPage.pageSize(),
                repoPage.itemCount(),
                repoPage.totalCount(),
                repoPage.hasNext(),
                repoPage.hasPrevious()
        );
    }
    
    /**
     * Get a specific repository from a VCS connection.
     * For REPOSITORY_TOKEN connections, uses the stored repository path.
     */
    public VcsRepositoryListDTO.VcsRepositoryDTO getRepository(Long workspaceId, Long connectionId, String externalRepoId) 
            throws IOException {
        
        VcsConnection connection = getConnection(workspaceId, connectionId);
        VcsClient client = createClientForConnection(connection);
        
        String externalWorkspaceId = getExternalWorkspaceId(connection);
        
        // For REPOSITORY_TOKEN connections, use stored repository path
        String effectiveRepoId = externalRepoId;
        if (connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN) {
            String repoPath = connection.getRepositoryPath();
            if (repoPath != null && !repoPath.isBlank()) {
                effectiveRepoId = repoPath;
                externalWorkspaceId = "";
            }
        }
        
        VcsRepository repo = client.getRepository(externalWorkspaceId, effectiveRepoId);
        if (repo == null) {
            throw new IntegrationException("Repository not found: " + effectiveRepoId);
        }
        
        boolean isOnboarded = bindingRepository.existsByProviderAndExternalRepoId(
                connection.getProviderType(), repo.id());
        
        return VcsRepositoryListDTO.VcsRepositoryDTO.fromModel(repo, isOnboarded);
    }

    /**
     * List branches in a repository with optional search and limit.
     * 
     * @param workspaceId The workspace ID
     * @param connectionId The VCS connection ID
     * @param externalRepoId The external repository ID or slug
     * @param search Optional search query to filter branch names
     * @param limit Maximum number of results (0 for unlimited)
     */
    public List<String> listBranches(Long workspaceId, Long connectionId, String externalRepoId,
                                      String search, int limit) throws IOException {
        
        VcsConnection connection = getConnection(workspaceId, connectionId);
        VcsClient client = createClientForConnection(connection);
        
        String externalWorkspaceId = getExternalWorkspaceId(connection);
        
        // For REPOSITORY_TOKEN connections, use stored repository path
        String effectiveRepoId = externalRepoId;
        if (connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN) {
            String repoPath = connection.getRepositoryPath();
            if (repoPath != null && !repoPath.isBlank()) {
                effectiveRepoId = repoPath;
                externalWorkspaceId = "";
            }
        }
        
        return client.listBranches(externalWorkspaceId, effectiveRepoId, search, limit);
    }
    
    /**
     * Onboard a repository (create project + binding + webhooks).
     * 
     * @param externalRepoId The repository slug (used for API calls) or UUID
     */
    @Transactional
    public RepoOnboardResponse onboardRepository(Long workspaceId, EVcsProvider provider,
                                                 String externalRepoId, RepoOnboardRequest request)
            throws IOException {
        
        VcsConnection connection = getConnection(workspaceId, request.getVcsConnectionId());
        
        // Validate provider matches
        if (connection.getProviderType() != provider) {
            throw new IntegrationException("Connection provider does not match URL provider");
        }
        
        VcsClient client = createClientForConnection(connection);
        String externalWorkspaceId = getExternalWorkspaceId(connection);
        
        // For REPOSITORY_TOKEN connections, use the stored repository path directly
        // This is needed because Project Access Tokens authenticate as a bot user,
        // not the actual namespace owner, so we can't use bot_username/repo
        String effectiveRepoId = externalRepoId;
        if (connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN) {
            String repoPath = connection.getRepositoryPath();
            if (repoPath != null && !repoPath.isBlank()) {
                // For repository tokens, the repoPath is the full path (e.g., "rostilos/codecrow-sample")
                // Use it directly since it's the only repo this token has access to
                log.debug("Using stored repositoryPath for REPOSITORY_TOKEN connection: {}", repoPath);
                effectiveRepoId = repoPath;
                // Also update externalWorkspaceId to be empty/ignored since we're using the full path
                externalWorkspaceId = "";
            }
        }
        //TODO: remove hardcode check
        // For GitLab OAuth (APP) connections, users can access repos from multiple namespaces
        // If the repoId is numeric (GitLab project ID) or contains a slash (full path),
        // use it directly without prepending the connection's externalWorkspaceId
        if (provider == EVcsProvider.GITLAB && connection.getConnectionType() == EVcsConnectionType.APP) {
            // Check if it's a numeric ID or contains a slash (full path like "namespace/repo")
            if (externalRepoId.matches("\\d+") || externalRepoId.contains("/")) {
                log.debug("GitLab OAuth: Using repoId directly (numeric or full path): {}", externalRepoId);
                externalWorkspaceId = "";
            }
        }
        
        log.debug("Onboarding repo: externalRepoId={}, externalWorkspaceId={}, connectionId={}, connectionType={}", 
                effectiveRepoId, externalWorkspaceId, connection.getId(), connection.getConnectionType());
        
        // Get repository details (externalRepoId can be slug or UUID, or full path for repository tokens)
        VcsRepository repo = client.getRepository(externalWorkspaceId, effectiveRepoId);
        if (repo == null) {
            log.warn("Repository not found: workspace={}, repo={}", externalWorkspaceId, effectiveRepoId);
            throw new IntegrationException("Repository not found: " + effectiveRepoId);
        }
        
        // Check if already onboarded using the stable UUID
        if (bindingRepository.existsByProviderAndExternalRepoId(provider, repo.id())) {
            throw new IntegrationException("Repository is already onboarded");
        }
        
        // Get or create project
        Project project;
        if (request.getProjectId() != null) {
            project = projectRepository.findById(request.getProjectId())
                    .orElseThrow(() -> new IntegrationException("Project not found"));
            if (!project.getWorkspace().getId().equals(workspaceId)) {
                throw new IntegrationException("Project does not belong to this workspace");
            }
        } else {
            project = createProject(workspaceId, request, repo);
        }
        
        if (request.getAiConnectionId() != null) {
            AIConnection aiConnection = aiConnectionRepository.findByWorkspace_IdAndId(workspaceId, request.getAiConnectionId())
                    .orElseThrow(() -> new IntegrationException("AI connection not found: " + request.getAiConnectionId()));
            
            ProjectAiConnectionBinding aiBinding = new ProjectAiConnectionBinding();
            aiBinding.setProject(project);
            aiBinding.setAiConnection(aiConnection);
            project.setAiConnectionBinding(aiBinding);
            project = projectRepository.save(project);
            log.info("Bound AI connection {} to project {}", aiConnection.getId(), project.getId());
        }
        
        // Create binding
        Workspace workspace = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new IntegrationException("Workspace not found"));
        
        VcsRepoBinding binding = new VcsRepoBinding();
        binding.setWorkspace(workspace);
        binding.setProject(project);
        binding.setVcsConnection(connection);
        binding.setProvider(provider);
        binding.setExternalRepoId(repo.id());
        binding.setExternalRepoSlug(repo.slug());
        binding.setExternalNamespace(repo.namespace());
        binding.setDisplayName(repo.name());
        binding.setDefaultBranch(repo.defaultBranch());
        
        // Setup webhooks if requested
        boolean webhooksConfigured = false;
        if (request.isSetupWebhooks()) {
            try {
                // Use the actual repo namespace for webhook setup, not the connection's externalWorkspaceId
                // This is important for GitLab OAuth where user can access repos from multiple namespaces
                String webhookWorkspaceId = repo.namespace();
                String webhookRepoSlug = repo.slug();
                
                // For REPOSITORY_TOKEN connections, use the full repo path for webhook setup
                if (connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN 
                        && connection.getRepositoryPath() != null 
                        && !connection.getRepositoryPath().isBlank()) {
                    String repositoryPath = connection.getRepositoryPath();
                    int lastSlash = repositoryPath.lastIndexOf('/');
                    if (lastSlash > 0) {
                        webhookWorkspaceId = repositoryPath.substring(0, lastSlash);
                        webhookRepoSlug = repositoryPath.substring(lastSlash + 1);
                    }
                    log.debug("REPOSITORY_TOKEN webhook setup - using repositoryPath: {}, namespace: {}, slug: {}", 
                            repositoryPath, webhookWorkspaceId, webhookRepoSlug);
                }
                
                log.debug("Setting up webhooks for repo: namespace={}, slug={}", webhookWorkspaceId, webhookRepoSlug);
                webhooksConfigured = setupWebhooks(client, webhookWorkspaceId, webhookRepoSlug, binding, project);
            } catch (Exception e) {
                log.warn("Failed to setup webhooks for {}: {}", repo.fullName(), e.getMessage());
            }
        }
        
        binding.setWebhooksConfigured(webhooksConfigured);
        VcsRepoBinding savedBinding = bindingRepository.save(binding);
        
        log.info("Onboarded repository {} to project {} in workspace {}", 
                repo.fullName(), project.getId(), workspaceId);
        
        return RepoOnboardResponse.success(
                project.getId(),
                project.getName(),
                project.getNamespace(),
                VcsRepoBindingDTO.fromEntity(savedBinding),
                webhooksConfigured
        );
    }
    
    private Project createProject(Long workspaceId, RepoOnboardRequest request, VcsRepository repo) {
        Workspace workspace = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new IntegrationException("Workspace not found"));
        
        Project project = new Project();
        project.setWorkspace(workspace);
        project.setName(request.getProjectName() != null ? request.getProjectName() : repo.name());
        project.setNamespace(request.getProjectNamespace() != null ? request.getProjectNamespace() : repo.slug());
        project.setDescription(request.getProjectDescription() != null ? request.getProjectDescription() : repo.description());
        project.setIsActive(true);
        
        if (request.getPrAnalysisEnabled() != null) {
            project.setPrAnalysisEnabled(request.getPrAnalysisEnabled());
        }
        if (request.getBranchAnalysisEnabled() != null) {
            project.setBranchAnalysisEnabled(request.getBranchAnalysisEnabled());
        }
        
        String mainBranch = request.getMainBranch();
        if (mainBranch == null || mainBranch.isBlank()) {
            // Fall back to repo's default branch
            mainBranch = repo.defaultBranch() != null ? repo.defaultBranch() : "main";
        }
        
        ProjectConfig config = new ProjectConfig(false, mainBranch);
        // Ensure main branch is always in analysis patterns
        config.ensureMainBranchInPatterns();
        project.setConfiguration(config);
        
        // Generate secure random auth token for webhooks (32 bytes = 256 bits of entropy)
        byte[] randomBytes = new byte[32];
        new SecureRandom().nextBytes(randomBytes);
        String authToken = Base64.getUrlEncoder().withoutPadding().encodeToString(randomBytes);
        project.setAuthToken(authToken);
        
        return projectRepository.save(project);
    }
    
    private boolean setupWebhooks(VcsClient client, String workspaceId, String repoSlug, 
                                   VcsRepoBinding binding, Project project) throws IOException {
        String webhookUrl = getWebhookUrl(binding.getProvider(), project);
        
        List<String> events = getWebhookEvents(binding.getProvider());
        String webhookId = client.ensureWebhook(workspaceId, repoSlug, webhookUrl, events);
        
        if (webhookId != null) {
            binding.setWebhookId(webhookId);
            return true;
        }
        return false;
    }
    
    private String getWebhookUrl(EVcsProvider provider, Project project) {
        var urls = siteSettingsProvider.getBaseUrlSettings();
        String base = (urls.webhookBaseUrl() != null && !urls.webhookBaseUrl().isBlank())
                ? urls.webhookBaseUrl() : urls.baseUrl();
        return base + "/api/webhooks/" + provider.getId() + "/" + project.getAuthToken();
    }
    
    private List<String> getWebhookEvents(EVcsProvider provider) {
        return switch (provider) {
            case BITBUCKET_CLOUD -> BITBUCKET_WEBHOOK_EVENTS;
            case GITHUB -> GITHUB_WEBHOOK_EVENTS;
            case GITLAB -> GITLAB_WEBHOOK_EVENTS;
            default -> List.of();
        };
    }
    
    /**
     * Get connections for a workspace, filtered by provider and optionally by connection type.
     */
    public List<VcsConnectionDTO> getConnections(Long workspaceId, EVcsProvider provider, EVcsConnectionType connectionType) {
        List<VcsConnection> connections;
        if (provider != null) {
            connections = connectionRepository.findByWorkspace_IdAndProviderType(workspaceId, provider);
        } else {
            connections = connectionRepository.findByWorkspace_Id(workspaceId);
        }
        
        // Filter by connection type if specified
        if (connectionType != null) {
            connections = connections.stream()
                    .filter(c -> c.getConnectionType() != null && connectionType.equals(c.getConnectionType()))
                    .toList();
        }
        
        return connections.stream()
                .map(VcsConnectionDTO::fromEntity)
                .toList();
    }
    
    /**
     * Get connections for a workspace (all connection types).
     */
    public List<VcsConnectionDTO> getConnections(Long workspaceId, EVcsProvider provider) {
        return getConnections(workspaceId, provider, null);
    }
    
    /**
     * Get only APP-based connections for a workspace.
     */
    public List<VcsConnectionDTO> getAppConnections(Long workspaceId, EVcsProvider provider) {
        List<VcsConnection> connections = connectionRepository
                .findByWorkspace_IdAndProviderType(workspaceId, provider);
        
        return connections.stream()
                .filter(c -> c.getConnectionType() == EVcsConnectionType.APP)
                .map(VcsConnectionDTO::fromEntity)
                .toList();
    }
    
    /**
     * Get a specific connection.
     */
    public VcsConnectionDTO getConnectionDTO(Long workspaceId, Long connectionId) {
        VcsConnection connection = getConnection(workspaceId, connectionId);
        return VcsConnectionDTO.fromEntity(connection);
    }
    
    /**
     * Delete a VCS connection.
     */
    @Transactional
    public void deleteConnection(Long workspaceId, Long connectionId) {
        VcsConnection connection = getConnection(workspaceId, connectionId);
        
        // Check if connection has any active bindings
        List<VcsRepoBinding> bindings = bindingRepository.findByVcsConnection_Id(connectionId);
        if (!bindings.isEmpty()) {
            throw new IntegrationException("Cannot delete connection with active repository bindings. " +
                    "Please remove all projects using this connection first.");
        }
        
        // Unlink any Connect App installation that references this connection
        // Must be done BEFORE deleting the connection due to foreign key constraint
        connectInstallationRepository.findByVcsConnection_Id(connectionId)
                .ifPresent(installation -> {
                    installation.setCodecrowWorkspace(null);
                    installation.setVcsConnection(null);
                    installation.setAccessToken(null);
                    installation.setRefreshToken(null);
                    installation.setTokenExpiresAt(null);
                    connectInstallationRepository.save(installation);
                    log.info("Unlinked BitbucketConnectInstallation {} for connection {}", 
                            installation.getId(), connectionId);
                });
        
        connectionRepository.delete(connection);
        log.info("Deleted VCS connection {} from workspace {}", connectionId, workspaceId);
    }
    
    /**
     * Sync a VCS connection (refresh status and repository count).
     */
    @Transactional
    public VcsConnectionDTO syncConnection(Long workspaceId, Long connectionId) {
        VcsConnection connection = getConnection(workspaceId, connectionId);
        
        try {
            VcsClient client = createClientForConnection(connection);
            
            String externalWorkspaceId = getExternalWorkspaceId(connection);
            
            if (externalWorkspaceId == null) {
                // Try to get workspace from Bitbucket
                List<VcsWorkspace> workspaces = client.listWorkspaces();
                if (!workspaces.isEmpty()) {
                    VcsWorkspace bbWorkspace = workspaces.get(0);
                    connection.setExternalWorkspaceId(bbWorkspace.id());
                    connection.setExternalWorkspaceSlug(bbWorkspace.slug());
                    externalWorkspaceId = bbWorkspace.slug();
                }
            }
            
            if (externalWorkspaceId != null) {
                int repoCount = client.getRepositoryCount(externalWorkspaceId);
                connection.setRepoCount(repoCount);
            }
            
            connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
            // updatedAt is automatically managed by @UpdateTimestamp
            
        } catch (Exception e) {
            log.warn("Failed to sync connection {}: {}", connectionId, e.getMessage());
            connection.setSetupStatus(EVcsSetupStatus.ERROR);
            // updatedAt is automatically managed by @UpdateTimestamp
        }
        
        VcsConnection saved = connectionRepository.save(connection);
        log.info("Synced VCS connection {} in workspace {}", connectionId, workspaceId);
        return VcsConnectionDTO.fromEntity(saved);
    }
    
    // ========== Helper Methods ==========
    
    private VcsConnection getConnection(Long workspaceId, Long connectionId) {
        return connectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IntegrationException("Connection not found"));
    }
    
    private VcsClient createClientForConnection(VcsConnection connection) {
        // VcsClientProvider.getClient() handles token refresh automatically for APP connections:
        // - Bitbucket APP: Uses refresh token
        // - GitHub APP: Uses installation token refresh via GitHub App private key
        // - GitHub OAuth: Tokens don't expire (tokenExpiresAt is null)
        return vcsClientProvider.getClient(connection);
    }
    
    /**
     * Get external workspace ID from connection - supports APP and OAUTH_MANUAL connection types.
     * For APP connections, uses the stored external workspace slug/id.
     * For OAUTH_MANUAL connections, gets from the BitbucketCloudConfig.
     * For REPOSITORY_TOKEN connections, extracts namespace from repositoryPath.
     */
    private String getExternalWorkspaceId(VcsConnection connection) {
        // For APP connections, use the stored external workspace slug/id
        if (connection.getConnectionType() == EVcsConnectionType.APP ||
            connection.getConnectionType() == EVcsConnectionType.CONNECT_APP ||
            connection.getConnectionType() == EVcsConnectionType.GITHUB_APP) {
            return connection.getExternalWorkspaceSlug() != null 
                    ? connection.getExternalWorkspaceSlug() 
                    : connection.getExternalWorkspaceId();
        }
        
        // For REPOSITORY_TOKEN connections, extract namespace from repositoryPath
        // Repository path is stored as "namespace/repo-name" (e.g., "rostilos/codecrow-sample")
        if (connection.getConnectionType() == EVcsConnectionType.REPOSITORY_TOKEN) {
            String repoPath = connection.getRepositoryPath();
            if (repoPath != null && repoPath.contains("/")) {
                return repoPath.substring(0, repoPath.lastIndexOf("/"));
            }
            // Fallback to stored values
            return connection.getExternalWorkspaceSlug() != null 
                    ? connection.getExternalWorkspaceSlug() 
                    : connection.getExternalWorkspaceId();
        }
        
        // For OAUTH_MANUAL connections (Bitbucket), get from config
        if (connection.getConfiguration() instanceof BitbucketCloudConfig config) {
            return config.workspaceId();
        }
        
        // Fallback to stored values
        return connection.getExternalWorkspaceSlug() != null 
                ? connection.getExternalWorkspaceSlug() 
                : connection.getExternalWorkspaceId();
    }
    
    private void validateProviderSupported(EVcsProvider provider) {
        if (provider != EVcsProvider.BITBUCKET_CLOUD && 
            provider != EVcsProvider.GITHUB && 
            provider != EVcsProvider.GITLAB) {
            throw new IntegrationException("Provider " + provider + " is not yet supported");
        }
    }
    
    private String generateState(EVcsProvider provider, Long workspaceId, Long connectionId) {
        return oAuthStateService.generateState(provider.getId(), workspaceId, connectionId);
    }
    
    // ========== Inner Classes ==========
    
    private record TokenResponse(String accessToken, String refreshToken, LocalDateTime expiresAt, String scopes) {}
}
