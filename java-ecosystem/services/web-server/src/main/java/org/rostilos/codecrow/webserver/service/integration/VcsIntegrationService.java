package org.rostilos.codecrow.webserver.service.integration;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.*;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
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
import org.rostilos.codecrow.webserver.dto.request.integration.RepoOnboardRequest;
import org.rostilos.codecrow.webserver.dto.response.integration.*;
import org.rostilos.codecrow.webserver.exception.IntegrationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.LocalDateTime;
import java.util.*;
import java.util.stream.Collectors;

/**
 * Service for VCS provider integrations.
 * Handles app installation, OAuth callbacks, repository listing, and onboarding.
 */
@Service
public class VcsIntegrationService {
    
    private static final Logger log = LoggerFactory.getLogger(VcsIntegrationService.class);
    
    // Bitbucket Cloud App events for webhooks
    private static final List<String> BITBUCKET_WEBHOOK_EVENTS = List.of(
        "pullrequest:created",
        "pullrequest:updated",
        "pullrequest:fulfilled",
        "pullrequest:rejected",
        "repo:push"
    );
    
    private final VcsConnectionRepository connectionRepository;
    private final VcsRepoBindingRepository bindingRepository;
    private final WorkspaceRepository workspaceRepository;
    private final ProjectRepository projectRepository;
    private final TokenEncryptionService encryptionService;
    private final HttpAuthorizedClientFactory httpClientFactory;
    private final VcsClientFactory vcsClientFactory;
    private final VcsClientProvider vcsClientProvider;
    
    @Value("${codecrow.bitbucket.app.client-id:}")
    private String bitbucketAppClientId;
    
    @Value("${codecrow.bitbucket.app.client-secret:}")
    private String bitbucketAppClientSecret;
    
    @Value("${codecrow.web.base.url:http://localhost:8081}")
    private String apiBaseUrl;
    
    @Value("${codecrow.webhook.base-url:}")
    private String webhookBaseUrl;
    
    public VcsIntegrationService(
            VcsConnectionRepository connectionRepository,
            VcsRepoBindingRepository bindingRepository,
            WorkspaceRepository workspaceRepository,
            ProjectRepository projectRepository,
            TokenEncryptionService encryptionService,
            HttpAuthorizedClientFactory httpClientFactory,
            VcsClientProvider vcsClientProvider
    ) {
        this.connectionRepository = connectionRepository;
        this.bindingRepository = bindingRepository;
        this.workspaceRepository = workspaceRepository;
        this.projectRepository = projectRepository;
        this.encryptionService = encryptionService;
        this.httpClientFactory = httpClientFactory;
        this.vcsClientFactory = new VcsClientFactory(httpClientFactory);
        this.vcsClientProvider = vcsClientProvider;
    }
    
    /**
     * Get the installation URL for a VCS provider app.
     */
    public InstallUrlResponse getInstallUrl(EVcsProvider provider, Long workspaceId) {
        validateProviderSupported(provider);
        
        if (provider == EVcsProvider.BITBUCKET_CLOUD) {
            return getBitbucketCloudInstallUrl(workspaceId);
        }
        
        throw new IntegrationException("Provider " + provider + " does not support app installation");
    }
    
    private InstallUrlResponse getBitbucketCloudInstallUrl(Long workspaceId) {
        if (bitbucketAppClientId == null || bitbucketAppClientId.isBlank()) {
            throw new IntegrationException(
                "Bitbucket Cloud App is not configured. " +
                "Please set 'codecrow.bitbucket.app.client-id' and 'codecrow.bitbucket.app.client-secret' " +
                "in your application.properties. See documentation for setup instructions."
            );
        }
        
        if (bitbucketAppClientSecret == null || bitbucketAppClientSecret.isBlank()) {
            throw new IntegrationException(
                "Bitbucket Cloud App client secret is not configured. " +
                "Please set 'codecrow.bitbucket.app.client-secret' in your application.properties."
            );
        }
        
        String state = generateState(EVcsProvider.BITBUCKET_CLOUD, workspaceId);
        String callbackUrl = apiBaseUrl + "/api/integrations/bitbucket-cloud/app/callback";
        
        log.info("Generated Bitbucket install URL with callback: {}", callbackUrl);
        
        String installUrl = "https://bitbucket.org/site/oauth2/authorize" +
                "?client_id=" + URLEncoder.encode(bitbucketAppClientId, StandardCharsets.UTF_8) +
                "&response_type=code" +
                "&redirect_uri=" + URLEncoder.encode(callbackUrl, StandardCharsets.UTF_8) +
                "&state=" + URLEncoder.encode(state, StandardCharsets.UTF_8);
        
        return new InstallUrlResponse(installUrl, EVcsProvider.BITBUCKET_CLOUD.getId(), state);
    }
    
    /**
     * Handle OAuth callback from a VCS provider app.
     */
    @Transactional
    public VcsConnectionDTO handleAppCallback(EVcsProvider provider, String code, String state, Long workspaceId) 
            throws GeneralSecurityException, IOException {
        validateProviderSupported(provider);
        
        if (provider == EVcsProvider.BITBUCKET_CLOUD) {
            return handleBitbucketCloudCallback(code, state, workspaceId);
        }
        
        throw new IntegrationException("Provider " + provider + " does not support app callback");
    }
    
    private VcsConnectionDTO handleBitbucketCloudCallback(String code, String state, Long workspaceId) 
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
        
        // Check for existing connection with same external workspace
        VcsConnection connection = null;
        if (bbWorkspace != null) {
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
                log.info("Updating existing Bitbucket Cloud App connection {} for workspace {}", 
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
            connection.setConnectionName("Bitbucket Cloud App");
        }
        
        VcsConnection saved = connectionRepository.save(connection);
        log.info("Saved Bitbucket Cloud App connection {} for workspace {}", saved.getId(), workspaceId);
        
        return VcsConnectionDTO.fromEntity(saved);
    }
    
    private TokenResponse exchangeBitbucketCode(String code) throws IOException {
        // Use OkHttp to exchange code for tokens
        okhttp3.OkHttpClient httpClient = new okhttp3.OkHttpClient();
        
        String credentials = Base64.getEncoder().encodeToString(
                (bitbucketAppClientId + ":" + bitbucketAppClientSecret).getBytes(StandardCharsets.UTF_8));
        
        String callbackUrl = apiBaseUrl + "/api/integrations/bitbucket-cloud/app/callback";
        
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
    
    /**
     * List repositories from a VCS connection.
     */
    public VcsRepositoryListDTO listRepositories(Long workspaceId, Long connectionId, String query, int page) 
            throws IOException {
        
        VcsConnection connection = getConnection(workspaceId, connectionId);
        VcsClient client = createClientForConnection(connection);
        
        String externalWorkspaceId = connection.getExternalWorkspaceSlug() != null 
                ? connection.getExternalWorkspaceSlug() 
                : connection.getExternalWorkspaceId();
        
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
     */
    public VcsRepositoryListDTO.VcsRepositoryDTO getRepository(Long workspaceId, Long connectionId, String externalRepoId) 
            throws IOException {
        
        VcsConnection connection = getConnection(workspaceId, connectionId);
        VcsClient client = createClientForConnection(connection);
        
        String externalWorkspaceId = connection.getExternalWorkspaceSlug() != null 
                ? connection.getExternalWorkspaceSlug() 
                : connection.getExternalWorkspaceId();
        
        VcsRepository repo = client.getRepository(externalWorkspaceId, externalRepoId);
        if (repo == null) {
            throw new IntegrationException("Repository not found: " + externalRepoId);
        }
        
        boolean isOnboarded = bindingRepository.existsByProviderAndExternalRepoId(
                connection.getProviderType(), repo.id());
        
        return VcsRepositoryListDTO.VcsRepositoryDTO.fromModel(repo, isOnboarded);
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
        String externalWorkspaceId = connection.getExternalWorkspaceSlug() != null 
                ? connection.getExternalWorkspaceSlug() 
                : connection.getExternalWorkspaceId();
        
        // Get repository details (externalRepoId can be slug or UUID)
        VcsRepository repo = client.getRepository(externalWorkspaceId, externalRepoId);
        if (repo == null) {
            throw new IntegrationException("Repository not found: " + externalRepoId);
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
                webhooksConfigured = setupWebhooks(client, externalWorkspaceId, repo.slug(), binding, project);
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
        
        // Generate auth token for webhooks
        project.setAuthToken(UUID.randomUUID().toString());
        
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
        String base = webhookBaseUrl != null && !webhookBaseUrl.isBlank() ? webhookBaseUrl : apiBaseUrl;
        return base + "/api/webhooks/" + provider.getId() + "/" + project.getAuthToken();
    }
    
    private List<String> getWebhookEvents(EVcsProvider provider) {
        return switch (provider) {
            case BITBUCKET_CLOUD -> BITBUCKET_WEBHOOK_EVENTS;
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
        return getConnections(workspaceId, provider, EVcsConnectionType.APP);
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
            
            String externalWorkspaceId = connection.getExternalWorkspaceSlug() != null 
                    ? connection.getExternalWorkspaceSlug() 
                    : connection.getExternalWorkspaceId();
            
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
        // Check if token needs refresh for APP connections
        if (connection.getConnectionType() == EVcsConnectionType.APP && needsTokenRefresh(connection)) {
            try {
                connection = refreshAccessToken(connection);
            } catch (IOException e) {
                log.error("Failed to refresh token for connection {}: {}", connection.getId(), e.getMessage());
                throw new IntegrationException("Access token expired and refresh failed: " + e.getMessage());
            }
        }
        
        // Use unified VcsClientProvider for all connection types
        return vcsClientProvider.getClient(connection);
    }
    
    /**
     * Check if the connection's token needs refresh.
     */
    private boolean needsTokenRefresh(VcsConnection connection) {
        if (connection.getTokenExpiresAt() == null) {
            return false;
        }
        return connection.getTokenExpiresAt().isBefore(LocalDateTime.now().plusMinutes(5));
    }
    
    /**
     * Refresh the access token for an APP connection.
     */
    @Transactional
    private VcsConnection refreshAccessToken(VcsConnection connection) throws IOException {
        if (connection.getRefreshToken() == null) {
            throw new IOException("No refresh token available for connection: " + connection.getId());
        }
        
        log.info("Refreshing access token for connection: {}", connection.getId());
        
        String decryptedRefreshToken;
        try {
            decryptedRefreshToken = encryptionService.decrypt(connection.getRefreshToken());
        } catch (GeneralSecurityException e) {
            throw new IOException("Failed to decrypt refresh token", e);
        }
        
        TokenResponse newTokens = refreshBitbucketToken(decryptedRefreshToken);
        
        try {
            connection.setAccessToken(encryptionService.encrypt(newTokens.accessToken()));
            if (newTokens.refreshToken() != null) {
                connection.setRefreshToken(encryptionService.encrypt(newTokens.refreshToken()));
            }
            connection.setTokenExpiresAt(newTokens.expiresAt());
            connection = connectionRepository.save(connection);
            
            log.info("Successfully refreshed access token for connection: {}", connection.getId());
            return connection;
        } catch (GeneralSecurityException e) {
            throw new IOException("Failed to encrypt new tokens", e);
        }
    }
    
    /**
     * Refresh Bitbucket access token using refresh token.
     */
    private TokenResponse refreshBitbucketToken(String refreshToken) throws IOException {
        okhttp3.OkHttpClient httpClient = new okhttp3.OkHttpClient();
        
        String credentials = Base64.getEncoder().encodeToString(
                (bitbucketAppClientId + ":" + bitbucketAppClientSecret).getBytes(StandardCharsets.UTF_8));
        
        okhttp3.RequestBody body = new okhttp3.FormBody.Builder()
                .add("grant_type", "refresh_token")
                .add("refresh_token", refreshToken)
                .build();
        
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url("https://bitbucket.org/site/oauth2/access_token")
                .header("Authorization", "Basic " + credentials)
                .post(body)
                .build();
        
        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                throw new IOException("Failed to refresh token: " + response.code() + " - " + errorBody);
            }
            
            String responseBody = response.body().string();
            com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
            com.fasterxml.jackson.databind.JsonNode json = mapper.readTree(responseBody);
            
            String accessToken = json.get("access_token").asText();
            String newRefreshToken = json.has("refresh_token") ? json.get("refresh_token").asText() : null;
            int expiresIn = json.has("expires_in") ? json.get("expires_in").asInt() : 7200;
            String scopes = json.has("scopes") ? json.get("scopes").asText() : null;
            
            LocalDateTime expiresAt = LocalDateTime.now().plusSeconds(expiresIn);
            
            return new TokenResponse(accessToken, newRefreshToken, expiresAt, scopes);
        }
    }
    
    private void validateProviderSupported(EVcsProvider provider) {
        if (provider != EVcsProvider.BITBUCKET_CLOUD) {
            throw new IntegrationException("Provider " + provider + " is not yet supported");
        }
    }
    
    private String generateState(EVcsProvider provider, Long workspaceId) {
        return Base64.getEncoder().encodeToString(
                (provider.getId() + ":" + workspaceId + ":" + System.currentTimeMillis()).getBytes(StandardCharsets.UTF_8));
    }
    
    // ========== Inner Classes ==========
    
    private record TokenResponse(String accessToken, String refreshToken, LocalDateTime expiresAt, String scopes) {}
}
