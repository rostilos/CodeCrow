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

import org.rostilos.codecrow.core.service.SiteSettingsProvider;

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
    private final SiteSettingsProvider siteSettingsProvider;
    
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
            SiteSettingsProvider siteSettingsProvider
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
    
    /**
     * Refresh the token for a GitHub App connection directly (server-side).
     * GitHub App installation tokens can be refreshed using the App's private key
     * without requiring user interaction (no OAuth redirect needed).
     * 
     * @param workspaceId the workspace that owns the connection
     * @param connectionId the connection to refresh
     * @return the updated connection DTO with fresh token
     */
    @Transactional
    public VcsConnectionDTO refreshConnectionToken(Long workspaceId, Long connectionId) {
        VcsConnection connection = connectionRepository.findById(connectionId)
                .orElseThrow(() -> new IntegrationException("Connection not found: " + connectionId));
        
        // Verify connection belongs to this workspace
        if (!connection.getWorkspace().getId().equals(workspaceId)) {
            throw new IntegrationException("Connection does not belong to this workspace");
        }
        
        EVcsProvider provider = connection.getProviderType();
        EVcsConnectionType connType = connection.getConnectionType();
        
        // Only GitHub App connections support server-side token refresh
        if (provider != EVcsProvider.GITHUB || connType != EVcsConnectionType.APP) {
            throw new IntegrationException(
                "Server-side token refresh is only supported for GitHub App connections. " +
                "Use the reconnect-url endpoint for OAuth-based connections."
            );
        }

        if (connection.getSetupStatus() == EVcsSetupStatus.PENDING) {
            throw new IntegrationException(
                    "A pending GitHub App installation cannot refresh its token before verification."
            );
        }
        
        String installationIdStr = getGitHubInstallationId(connection);
        if (installationIdStr == null || installationIdStr.isBlank()) {
            throw new IntegrationException("No installation ID found for GitHub App connection: " + connectionId);
        }
        
        long installationId;
        try {
            installationId = Long.parseLong(installationIdStr);
        } catch (NumberFormatException e) {
            throw new IntegrationException("Invalid installation ID for connection: " + connectionId);
        }
        
        try {
            log.info("Refreshing token for GitHub App connection {} (installation: {})", connectionId, installationId);
            
            org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService authService =
                    createGitHubAppAuthService();
            var installationToken = authService.getInstallationAccessToken(installationId);
            
            // Update connection with new token
            connection.setAccessToken(encryptionService.encrypt(installationToken.token()));
            connection.setTokenExpiresAt(installationToken.expiresAt());
            connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
            
            // Also refresh repo count
            try {
                VcsClient client = vcsClientFactory.createClient(EVcsProvider.GITHUB, installationToken.token(), null);
                String accountLogin = connection.getExternalWorkspaceSlug();
                if (accountLogin != null && !accountLogin.isBlank()) {
                    int repoCount = client.getRepositoryCount(accountLogin);
                    connection.setRepoCount(repoCount);
                }
            } catch (Exception e) {
                log.warn("Could not refresh repo count during token refresh for connection {}: {}", 
                        connectionId, e.getMessage());
            }
            
            VcsConnection saved = connectionRepository.save(connection);
            log.info("Successfully refreshed GitHub App token for connection {} (expires: {})", 
                    saved.getId(), saved.getTokenExpiresAt());
            
            return VcsConnectionDTO.fromEntity(saved);
            
        } catch (Exception e) {
            log.error("Failed to refresh GitHub App token for connection {}: {}", connectionId, e.getMessage(), e);
            throw new IntegrationException("Failed to refresh token: " + e.getMessage());
        }
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
        var githubSettings = siteSettingsProvider.getGitHubSettings();
        String githubSlug = githubSettings.slug();
        if (githubSlug != null && !githubSlug.isBlank()) {
            boolean hasPrivateKey = (githubSettings.privateKeyContent() != null
                    && !githubSettings.privateKeyContent().isBlank())
                    || (githubSettings.privateKeyPath() != null
                    && !githubSettings.privateKeyPath().isBlank());
            if (githubSettings.appId() == null || githubSettings.appId().isBlank()
                    || !hasPrivateKey
                    || githubSettings.oauthClientId() == null
                    || githubSettings.oauthClientId().isBlank()
                    || githubSettings.oauthClientSecret() == null
                    || githubSettings.oauthClientSecret().isBlank()) {
                throw new IntegrationException(
                        "GitHub App installation is incomplete. Configure the App ID, private key, " +
                        "Client ID, and Client Secret in Site Administration."
                );
            }

            if (connectionId != null) {
                VcsConnection requested = getConnection(workspaceId, connectionId);
                if (requested.getProviderType() == EVcsProvider.GITHUB
                        && requested.getConnectionType() == EVcsConnectionType.APP
                        && requested.getSetupStatus() == EVcsSetupStatus.PENDING
                        && requested.getGithubInstallationRequestId() != null) {
                    throw new IntegrationException(
                            "This connection is already bound to a GitHub installation request. " +
                            "Check its approval status instead of starting another request."
                    );
                }
                String currentInstallationId = getGitHubInstallationId(requested);
                if (requested.getProviderType() == EVcsProvider.GITHUB
                        && requested.getConnectionType() == EVcsConnectionType.APP
                        && requested.getSetupStatus() == EVcsSetupStatus.CONNECTED
                        && currentInstallationId != null
                        && currentInstallationId.matches("\\d+")) {
                    return getGitHubInstallationVerificationUrl(
                            workspaceId, connectionId, Long.parseLong(currentInstallationId));
                }
            }

            // Identify the GitHub requester before sending them to the App
            // installation page. This lets us snapshot existing GitHub request
            // IDs and later bind only the exact new request to this workspace.
            return getGitHubUserAuthorizationUrl(
                    workspaceId,
                    connectionId,
                    null,
                    OAuthStateService.GITHUB_INSTALL_START);
        }
        
        // Fallback to OAuth flow (limited to public repos unless user grants repo scope)
        String githubOAuthClientId = githubSettings.oauthClientId();
        String githubOAuthClientSecret = githubSettings.oauthClientSecret();
        
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
        if (stateData == null) {
            throw new IntegrationException("Invalid or expired OAuth state");
        }
        if (!provider.getId().equals(stateData.providerId())) {
            throw new IntegrationException("OAuth state provider mismatch");
        }
        if (!workspaceId.equals(stateData.workspaceId())) {
            throw new IntegrationException("OAuth state workspace mismatch");
        }

        Long connectionId = stateData.connectionId();
        
        if (connectionId != null) {
            log.info("Processing reconnection callback for connection {} (provider: {})", connectionId, provider);
        }
        
        return switch (provider) {
            case BITBUCKET_CLOUD -> handleBitbucketCloudCallback(code, state, workspaceId, connectionId);
            case GITHUB -> handleGitHubCallback(
                    code, state, workspaceId, connectionId, stateData.installationId(), stateData.purpose());
            case GITLAB -> handleGitLabCallback(code, state, workspaceId, connectionId);
            default -> throw new IntegrationException("Provider " + provider + " does not support app callback");
        };
    }
    
    /**
     * Prepare a GitHub App installation callback for user verification.
     * The callback's installation ID is not authorization proof, so this method
     * selects or creates only the workspace connection and does not persist that
     * ID. Association and token activation happen after GitHub user OAuth.
     */
    @Transactional
    public VcsConnectionDTO handleGitHubAppInstallation(
            Long installationId,
            Long workspaceId,
            Long requestedConnectionId) {
        if (installationId == null || installationId <= 0) {
            throw new IntegrationException("Invalid GitHub App installation ID");
        }

        Workspace workspace = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new IntegrationException("Workspace not found"));
        VcsConnection connection = null;

        // Signed reconnection state names the exact row. Never infer it from
        // globally visible installations or from the number of PENDING rows.
        if (requestedConnectionId != null) {
            connection = getConnection(workspaceId, requestedConnectionId);
            if (connection.getProviderType() != EVcsProvider.GITHUB
                    || connection.getConnectionType() != EVcsConnectionType.APP) {
                throw new IntegrationException("Connection is not a GitHub App connection");
            }

            String requestedInstallationId = getGitHubInstallationId(connection);
            if (connection.getSetupStatus() != EVcsSetupStatus.PENDING
                    && requestedInstallationId != null
                    && !requestedInstallationId.isBlank()
                    && !requestedInstallationId.equals(String.valueOf(installationId))) {
                throw new IntegrationException(
                        "GitHub App installation does not match the connection being reconnected"
                );
            }

            if (connection.getSetupStatus() == EVcsSetupStatus.PENDING) {
                if (connection.getGithubInstallationRequestId() != null) {
                    throw new IntegrationException(
                            "This connection is awaiting approval for a different, request-bound GitHub flow."
                    );
                }
                // Release legacy pre-fix associations. A spoofable setup callback
                // must not be able to reserve an installation for any tenant.
                connection.setInstallationId(null);
                connection.setExternalWorkspaceId(null);
                connection.setExternalWorkspaceSlug(null);
                connection.setConnectionName("GitHub – Pending Verification");
                connection = connectionRepository.save(connection);
            }
        }

        if (connection == null) {
            connection = new VcsConnection();
            connection.setWorkspace(workspace);
            connection.setProviderType(EVcsProvider.GITHUB);
            connection.setConnectionType(EVcsConnectionType.APP);
            connection.setSetupStatus(EVcsSetupStatus.PENDING);
            connection.setConnectionName("GitHub – Pending Verification");
            connection = connectionRepository.save(connection);
        }

        log.info("Prepared GitHub App connection {} for requester verification in workspace {}",
                connection.getId(), workspaceId);
        return VcsConnectionDTO.fromEntity(connection);
    }

    /**
     * Start GitHub user authorization for an exact installation and connection.
     * Both IDs are carried in signed, expiring state; the installation ID is not
     * persisted until GitHub confirms the user's authority over it.
     */
    public InstallUrlResponse getGitHubInstallationVerificationUrl(
            Long workspaceId,
            Long connectionId,
            Long installationId) {
        VcsConnection connection = getConnection(workspaceId, connectionId);
        if (connection.getProviderType() != EVcsProvider.GITHUB
                || connection.getConnectionType() != EVcsConnectionType.APP
                || installationId == null
                || installationId <= 0) {
            throw new IntegrationException("GitHub App connection is not ready for installation verification");
        }

        var settings = siteSettingsProvider.getGitHubSettings();
        if (settings.oauthClientId() == null || settings.oauthClientId().isBlank()
                || settings.oauthClientSecret() == null || settings.oauthClientSecret().isBlank()) {
            throw new IntegrationException(
                    "GitHub App user authorization is required. Configure the GitHub App Client ID " +
                    "and Client Secret in Site Administration."
            );
        }

        return getGitHubUserAuthorizationUrl(
                workspaceId,
                connectionId,
                installationId,
                OAuthStateService.GITHUB_INSTALL_VERIFY);
    }

    private InstallUrlResponse getGitHubUserAuthorizationUrl(
            Long workspaceId,
            Long connectionId,
            Long installationId,
            String purpose) {
        var settings = siteSettingsProvider.getGitHubSettings();
        if (settings.oauthClientId() == null || settings.oauthClientId().isBlank()
                || settings.oauthClientSecret() == null || settings.oauthClientSecret().isBlank()) {
            throw new IntegrationException(
                    "GitHub App user authorization is required. Configure the GitHub App Client ID " +
                    "and Client Secret in Site Administration."
            );
        }

        String state = oAuthStateService.generateState(
                EVcsProvider.GITHUB.getId(), workspaceId, connectionId, installationId, purpose);
        String callbackUrl = siteSettingsProvider.getBaseUrlSettings().baseUrl()
                + "/api/integrations/github/app/callback";
        String authorizeUrl = "https://github.com/login/oauth/authorize"
                + "?client_id=" + URLEncoder.encode(settings.oauthClientId(), StandardCharsets.UTF_8)
                + "&redirect_uri=" + URLEncoder.encode(callbackUrl, StandardCharsets.UTF_8)
                + "&state=" + URLEncoder.encode(state, StandardCharsets.UTF_8)
                + "&prompt=select_account";

        return new InstallUrlResponse(authorizeUrl, EVcsProvider.GITHUB.getId(), state);
    }

    /**
     * Authenticate the GitHub requester before the installation selection page.
     * Only a new/reused PENDING row is changed; an existing connected
     * installation is never reset or re-synchronized by this flow.
     */
    @Transactional
    public InstallUrlResponse beginGitHubAppInstallation(
            String code,
            String state,
            Long workspaceId) throws GeneralSecurityException, IOException {
        OAuthStateService.OAuthStateData stateData = oAuthStateService.validateAndExtractState(state);
        if (stateData == null
                || !EVcsProvider.GITHUB.getId().equals(stateData.providerId())
                || !workspaceId.equals(stateData.workspaceId())
                || !OAuthStateService.GITHUB_INSTALL_START.equals(stateData.purpose())) {
            throw new IntegrationException("Invalid or expired GitHub installation state");
        }

        TokenResponse tokens = exchangeGitHubCode(code);
        var authService = createGitHubAppAuthService();
        var requester = authService.getAuthenticatedUser(tokens.accessToken);
        Set<String> existingRequestIds = authService.listInstallationRequests().stream()
                .filter(request -> request.requesterId() == requester.id())
                .map(request -> String.valueOf(request.requestId()))
                .collect(Collectors.toCollection(TreeSet::new));

        Workspace workspace = workspaceRepository.findById(workspaceId)
                .orElseThrow(() -> new IntegrationException("Workspace not found"));
        VcsConnection pending = null;
        if (stateData.connectionId() != null) {
            VcsConnection requested = getConnection(workspaceId, stateData.connectionId());
            if (requested.getProviderType() == EVcsProvider.GITHUB
                    && requested.getConnectionType() == EVcsConnectionType.APP
                    && requested.getSetupStatus() == EVcsSetupStatus.PENDING) {
                pending = requested;
            }
        }
        if (pending == null) {
            pending = new VcsConnection();
            pending.setWorkspace(workspace);
            pending.setProviderType(EVcsProvider.GITHUB);
            pending.setConnectionType(EVcsConnectionType.APP);
            pending.setSetupStatus(EVcsSetupStatus.PENDING);
        }

        pending.setConnectionName("GitHub – Select Organization");
        pending.setInstallationId(null);
        pending.setExternalWorkspaceId(null);
        pending.setExternalWorkspaceSlug(null);
        pending.setGithubInstallationRequestId(null);
        pending.setGithubInstallationRequesterId(String.valueOf(requester.id()));
        pending.setGithubInstallationRequestSnapshot(String.join(",", existingRequestIds));
        pending.setGithubInstallationRequestStartedAt(LocalDateTime.now());
        pending = connectionRepository.save(pending);

        String selectionState = oAuthStateService.generateState(
                EVcsProvider.GITHUB.getId(),
                workspaceId,
                pending.getId(),
                null,
                OAuthStateService.GITHUB_INSTALL_SELECT);
        String installUrl = "https://github.com/apps/" +
                siteSettingsProvider.getGitHubSettings().slug() +
                "/installations/new?state=" +
                URLEncoder.encode(selectionState, StandardCharsets.UTF_8);
        log.info("Bound GitHub installation selection {} in workspace {} to requester {}",
                pending.getId(), workspaceId, requester.login());
        return new InstallUrlResponse(installUrl, EVcsProvider.GITHUB.getId(), selectionState);
    }

    /**
     * Bind setup_action=request to the exact new GitHub request created after
     * this workspace's pre-install snapshot. No global installation is selected.
     */
    @Transactional
    public VcsConnectionDTO handleGitHubAppInstallationRequest(
            Long workspaceId,
            Long connectionId) throws IOException {
        VcsConnection pending = getConnection(workspaceId, connectionId);
        if (pending.getProviderType() != EVcsProvider.GITHUB
                || pending.getConnectionType() != EVcsConnectionType.APP
                || pending.getSetupStatus() != EVcsSetupStatus.PENDING
                || pending.getGithubInstallationRequesterId() == null
                || pending.getGithubInstallationRequestStartedAt() == null) {
            throw new IntegrationException("GitHub installation request is not bound to this connection");
        }

        Set<String> snapshot = parseRequestSnapshot(pending.getGithubInstallationRequestSnapshot());
        long requesterId = Long.parseLong(pending.getGithubInstallationRequesterId());
        List<org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService.InstallationRequestInfo> matches =
                createGitHubAppAuthService().listInstallationRequests().stream()
                        .filter(request -> request.requesterId() == requesterId)
                        .filter(request -> !snapshot.contains(String.valueOf(request.requestId())))
                        .toList();

        if (matches.size() != 1) {
            log.warn("SECURITY: Expected one new GitHub installation request for connection {}, found {}",
                    connectionId, matches.size());
            throw new IntegrationException(
                    "CodeCrow could not uniquely identify the GitHub installation request. " +
                    "Start the connection request again from this workspace."
            );
        }

        var request = matches.get(0);
        pending.setGithubInstallationRequestId(String.valueOf(request.requestId()));
        pending.setExternalWorkspaceId(String.valueOf(request.accountId()));
        pending.setExternalWorkspaceSlug(request.accountLogin());
        pending.setConnectionName("GitHub – " + request.accountLogin() + " (Pending Approval)");
        pending.setGithubInstallationRequestSnapshot(null);
        VcsConnection saved = connectionRepository.save(pending);
        log.info("Bound connection {} in workspace {} to GitHub request {} for account {} ({})",
                connectionId, workspaceId, request.requestId(), request.accountLogin(), request.accountId());
        return VcsConnectionDTO.fromEntity(saved);
    }

    private Set<String> parseRequestSnapshot(String snapshot) {
        if (snapshot == null || snapshot.isBlank()) {
            return Set.of();
        }
        return Arrays.stream(snapshot.split(","))
                .filter(value -> !value.isBlank())
                .collect(Collectors.toSet());
    }

    /**
     * Reconcile an installation.created webhook with an already verified link
     * or one exact request-bound target account.
     * 
     * This method does NOT require a user session — the org owner may not have
     * a CodeCrow account. A new row is eligible only when it already stores
     * GitHub's exact request ID, requester ID, and target account ID.
     */
    @Transactional
    public VcsConnectionDTO completeGitHubAppInstallation(
            long installationId, long accountId, String accountLogin, String accountType)
            throws GeneralSecurityException, IOException {
        
        // Check if this installation already exists as a connected connection
        Optional<VcsConnection> existingInstallation = findGitHubAppConnectionByInstallationId(installationId);
        
        if (existingInstallation.isPresent() && 
                existingInstallation.get().getSetupStatus() == EVcsSetupStatus.CONNECTED) {
            VcsConnection connection = existingInstallation.get();
            boolean changed = false;
            if (connection.getInstallationId() == null || connection.getInstallationId().isBlank()) {
                connection.setInstallationId(String.valueOf(installationId));
                changed = true;
            }
            if ((connection.getExternalWorkspaceSlug() == null || connection.getExternalWorkspaceSlug().isBlank())
                    && accountLogin != null && !accountLogin.isBlank()) {
                connection.setExternalWorkspaceSlug(accountLogin);
                connection.setConnectionName("GitHub – " + accountLogin);
                changed = true;
            }
            if (changed) {
                connection = connectionRepository.save(connection);
            }
            log.info("Installation {} already connected as connection {}", installationId, connection.getId());
            return VcsConnectionDTO.fromEntity(connection);
        }
        
        if (existingInstallation.isEmpty()) {
            List<VcsConnection> requestBound = connectionRepository
                    .findByProviderTypeAndConnectionTypeAndSetupStatusAndExternalWorkspaceIdAndGithubInstallationRequestIdIsNotNull(
                            EVcsProvider.GITHUB,
                            EVcsConnectionType.APP,
                            EVcsSetupStatus.PENDING,
                            String.valueOf(accountId));
            if (requestBound.size() > 1) {
                Set<String> stillPendingRequestIds = createGitHubAppAuthService()
                        .listInstallationRequests().stream()
                        .map(request -> String.valueOf(request.requestId()))
                        .collect(Collectors.toSet());
                requestBound = requestBound.stream()
                        .filter(connection -> !stillPendingRequestIds.contains(
                                connection.getGithubInstallationRequestId()))
                        .toList();
            }
            if (requestBound.size() == 1) {
                VcsConnection connection = requestBound.get(0);
                String requestId = connection.getGithubInstallationRequestId();
                VcsConnection saved = activateGitHubAppConnection(
                        connection,
                        installationId,
                        accountLogin,
                        accountType,
                        createGitHubAppAuthService());
                log.info("Activated installation {} for exact GitHub request {} on connection {}",
                        installationId,
                        requestId,
                        connection.getId());
                return VcsConnectionDTO.fromEntity(saved);
            }

            log.warn("SECURITY: Refusing to auto-link GitHub App installation {} for account {}: " +
                            "found {} exact request-bound workspace candidates",
                    installationId, accountId, requestBound.size());
            throw new IntegrationException(
                    requestBound.isEmpty()
                            ? "GitHub App installation is not associated with a CodeCrow workspace request."
                            : "Multiple CodeCrow requests target this GitHub account; explicit verification is required."
            );
        }

        VcsConnection connection = existingInstallation.get();
        log.warn("SECURITY: Installation {} is pre-associated with connection {} but remains {}. " +
                        "A webhook cannot substitute for a bound GitHub verification flow.",
                installationId, connection.getId(), connection.getSetupStatus());
        throw new IntegrationException(
                "GitHub verification is required from the intended workspace dashboard."
        );
    }

    /**
     * Handle GitHub App installation removal (deleted or suspended).
     * Marks the connection as DISABLED so the user knows it was removed on GitHub's side.
     */
    @Transactional
    public void handleGitHubAppInstallationRemoved(long installationId) {
        connectionRepository.findByProviderTypeAndInstallationId(
                EVcsProvider.GITHUB, String.valueOf(installationId))
                .ifPresent(connection -> {
                    connection.setSetupStatus(EVcsSetupStatus.DISABLED);
                    connectionRepository.save(connection);
                    log.info("Marked connection {} as DISABLED (installation {} removed)", 
                            connection.getId(), installationId);
                });
        
        // Also check by externalWorkspaceId (older connections may not have installationId set)
        connectionRepository.findByProviderTypeAndExternalWorkspaceId(
                EVcsProvider.GITHUB, String.valueOf(installationId))
                .stream()
                .filter(c -> c.getConnectionType() == EVcsConnectionType.APP)
                .filter(c -> c.getGithubInstallationRequestId() == null)
                .forEach(connection -> {
                    if (connection.getSetupStatus() != EVcsSetupStatus.DISABLED) {
                        connection.setSetupStatus(EVcsSetupStatus.DISABLED);
                        connectionRepository.save(connection);
                        log.info("Marked connection {} as DISABLED (installation {} removed, matched by externalWorkspaceId)", 
                                connection.getId(), installationId);
                    }
                });
    }

    private org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService createGitHubAppAuthService() {
        var ghSettings = siteSettingsProvider.getGitHubSettings();
        String ghAppId = ghSettings.appId();
        String ghPrivateKeyPath = ghSettings.privateKeyPath();
        String ghPrivateKeyContent = ghSettings.privateKeyContent();

        if (ghAppId == null || ghAppId.isBlank()) {
            throw new IntegrationException(
                    "GitHub App is not configured. Please configure GitHub App settings in Site Admin."
            );
        }

        try {
            if (ghPrivateKeyContent != null && !ghPrivateKeyContent.isBlank()) {
                java.security.PrivateKey privateKey =
                        org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService
                                .parsePrivateKeyContent(ghPrivateKeyContent);
                return new org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService(ghAppId, privateKey);
            }
            if (ghPrivateKeyPath != null && !ghPrivateKeyPath.isBlank()) {
                return new org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService(ghAppId, ghPrivateKeyPath);
            }
        } catch (Exception e) {
            throw new IntegrationException("Failed to initialize GitHub App auth: " + e.getMessage());
        }

        throw new IntegrationException(
                "GitHub App private key not configured. Upload a .pem file in Site Admin → GitHub settings."
        );
    }

    private String getGitHubInstallationId(VcsConnection connection) {
        if (connection.getInstallationId() != null && !connection.getInstallationId().isBlank()) {
            return connection.getInstallationId();
        }
        return connection.getExternalWorkspaceId();
    }

    private Optional<VcsConnection> findGitHubAppConnectionByInstallationId(long installationId) {
        String installationIdStr = String.valueOf(installationId);

        Optional<VcsConnection> byInstallationId = connectionRepository
                .findByProviderTypeAndInstallationId(EVcsProvider.GITHUB, installationIdStr);
        if (byInstallationId.isPresent()) {
            return byInstallationId;
        }

        return connectionRepository
                .findByProviderTypeAndExternalWorkspaceId(EVcsProvider.GITHUB, installationIdStr)
                .stream()
                .filter(c -> c.getConnectionType() == EVcsConnectionType.APP)
                .filter(c -> c.getGithubInstallationRequestId() == null)
                .findFirst();
    }

    private VcsConnection activateGitHubAppConnection(
            VcsConnection connection,
            long installationId,
            String accountLogin,
            String accountType,
            org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService authService)
            throws GeneralSecurityException, IOException {

        var installationToken = authService.getInstallationAccessToken(installationId);
        boolean wasPendingApproval = connection.getSetupStatus() == EVcsSetupStatus.PENDING
                || (connection.getConnectionName() != null && connection.getConnectionName().contains("Pending"));

        connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
        connection.setAccessToken(encryptionService.encrypt(installationToken.token()));
        connection.setTokenExpiresAt(installationToken.expiresAt());
        connection.setExternalWorkspaceId(String.valueOf(installationId));
        connection.setInstallationId(String.valueOf(installationId));
        connection.setGithubInstallationRequestId(null);
        connection.setGithubInstallationRequesterId(null);
        connection.setGithubInstallationRequestSnapshot(null);
        connection.setGithubInstallationRequestStartedAt(null);
        connection.setGithubBindingVerifiedAt(LocalDateTime.now());

        if (accountLogin != null && !accountLogin.isBlank()) {
            connection.setExternalWorkspaceSlug(accountLogin);
            connection.setConnectionName("GitHub – " + accountLogin);
        } else if (connection.getConnectionName() == null
                || connection.getConnectionName().isBlank()
                || wasPendingApproval) {
            connection.setConnectionName("GitHub – Installation " + installationId);
        }

        try {
            if (accountLogin != null && !accountLogin.isBlank()) {
                VcsClient client = vcsClientFactory.createClient(EVcsProvider.GITHUB, installationToken.token(), null);
                int repoCount = client.getRepositoryCount(accountLogin);
                connection.setRepoCount(repoCount);
            }
        } catch (Exception e) {
            log.warn("Could not fetch repo count for GitHub App installation {} (accountType={}): {}",
                    installationId, accountType, e.getMessage());
        }

        return connectionRepository.save(connection);
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
    
    private VcsConnectionDTO handleGitHubCallback(
            String code,
            String state,
            Long workspaceId,
            Long connectionId,
            Long verifiedFlowInstallationId,
            String statePurpose)
            throws GeneralSecurityException, IOException {
        
        TokenResponse tokens = exchangeGitHubCode(code);

        if (connectionId != null) {
            VcsConnection installationConnection = getConnection(workspaceId, connectionId);
            if (installationConnection.getProviderType() == EVcsProvider.GITHUB
                    && installationConnection.getConnectionType() == EVcsConnectionType.APP
                    && verifiedFlowInstallationId != null
                    && OAuthStateService.GITHUB_INSTALL_VERIFY.equals(statePurpose)) {
                long parsedInstallationId = verifiedFlowInstallationId;

                org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService authService =
                        createGitHubAppAuthService();
                var installation = authService.getInstallation(parsedInstallationId);
                var user = authService.getAuthenticatedUser(tokens.accessToken);

                if (installationConnection.getGithubInstallationRequesterId() != null
                        && !installationConnection.getGithubInstallationRequesterId()
                        .equals(String.valueOf(user.id()))) {
                    log.warn("SECURITY: GitHub installation requester {} does not match verifying user {} " +
                                    "for connection {}",
                            installationConnection.getGithubInstallationRequesterId(),
                            user.id(), installationConnection.getId());
                    throw new IntegrationException(
                            "Sign in with the same GitHub account that started this installation request."
                    );
                }

                if (!authService.canUserAccessInstallation(tokens.accessToken, installation)) {
                    log.warn("SECURITY: GitHub user authorization could not prove access " +
                                    "to installation {} ({}) for workspace {}",
                            parsedInstallationId, installation.accountLogin(), workspaceId);
                    throw new IntegrationException(
                            "The authorized GitHub user cannot access the selected GitHub App installation."
                    );
                }

                if (installationConnection.getGithubInstallationRequestId() != null
                        && !String.valueOf(installation.accountId())
                        .equals(installationConnection.getExternalWorkspaceId())) {
                    log.warn("SECURITY: Installation {} account {} does not match request-bound account {} " +
                                    "for connection {}",
                            parsedInstallationId,
                            installation.accountId(),
                            installationConnection.getExternalWorkspaceId(),
                            installationConnection.getId());
                    throw new IntegrationException(
                            "The approved GitHub installation does not match the organization that was requested."
                    );
                }

                Optional<VcsConnection> existingInstallation =
                        findGitHubAppConnectionByInstallationId(parsedInstallationId);
                if (existingInstallation.isPresent()
                        && !Objects.equals(existingInstallation.get().getId(), installationConnection.getId())) {
                    log.warn("SECURITY: Verified installation {} is already linked to connection {} " +
                                    "and cannot be attached to connection {} in workspace {}",
                            parsedInstallationId,
                            existingInstallation.get().getId(),
                            installationConnection.getId(),
                            workspaceId);
                    throw new IntegrationException(
                            "GitHub App installation is already linked to another CodeCrow workspace."
                    );
                }

                // The user token is authorization proof only. Never store it as the
                // workspace credential; mint a fresh token for the verified installation.
                installationConnection.setRefreshToken(null);
                installationConnection.setScopes(null);
                VcsConnection saved = activateGitHubAppConnection(
                        installationConnection,
                        parsedInstallationId,
                        installation.accountLogin(),
                        installation.accountType(),
                        authService);
                log.info("Verified GitHub installation access and activated installation {} ({}) " +
                                "for connection {} in workspace {}",
                        parsedInstallationId, installation.accountLogin(), saved.getId(), workspaceId);
                return VcsConnectionDTO.fromEntity(saved);
            }

            if (verifiedFlowInstallationId != null) {
                throw new IntegrationException("Invalid GitHub installation verification state");
            }
        }
        
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
                .add("client_id", siteSettingsProvider.getGitHubSettings().oauthClientId())
                .add("client_secret", siteSettingsProvider.getGitHubSettings().oauthClientSecret())
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

            if (!json.hasNonNull("access_token")
                    || json.path("access_token").asText().isBlank()) {
                throw new IOException("GitHub OAuth response did not contain an access token");
            }
            
            String accessToken = json.path("access_token").asText();
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
        try {
            project.setAuthToken(encryptionService.encrypt(authToken));
        } catch (java.security.GeneralSecurityException e) {
            log.warn("Failed to encrypt auth token for new project, storing plaintext", e);
            project.setAuthToken(authToken);
        }
        
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
        // Try decrypting the stored token; fall back to raw value for legacy plaintext tokens
        String plainToken;
        try {
            plainToken = encryptionService.decrypt(project.getAuthToken());
        } catch (Exception e) {
            plainToken = project.getAuthToken();
        }
        return base + "/api/webhooks/" + provider.getId() + "/" + plainToken;
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

        if (connection.getProviderType() == EVcsProvider.GITHUB
                && connection.getConnectionType() == EVcsConnectionType.APP
                && connection.getSetupStatus() == EVcsSetupStatus.PENDING) {
            return syncPendingGitHubAppConnection(connection);
        }
        
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

    private VcsConnectionDTO syncPendingGitHubAppConnection(VcsConnection connection) {
        if (connection.getGithubInstallationRequestId() == null
                || connection.getExternalWorkspaceId() == null) {
            log.info("GitHub App connection {} remains pending until its installation flow is verified",
                    connection.getId());
            return VcsConnectionDTO.fromEntity(connection);
        }

        try {
            var authService = createGitHubAppAuthService();
            Set<String> stillPendingRequestIds = authService.listInstallationRequests().stream()
                    .map(request -> String.valueOf(request.requestId()))
                    .collect(Collectors.toSet());
            boolean requestStillPending = stillPendingRequestIds.contains(
                    connection.getGithubInstallationRequestId());
            if (requestStillPending) {
                log.info("GitHub installation request {} for connection {} is still awaiting approval",
                        connection.getGithubInstallationRequestId(), connection.getId());
                return VcsConnectionDTO.fromEntity(connection);
            }

            List<VcsConnection> accountCandidates = connectionRepository
                    .findByProviderTypeAndConnectionTypeAndSetupStatusAndExternalWorkspaceIdAndGithubInstallationRequestIdIsNotNull(
                            EVcsProvider.GITHUB,
                            EVcsConnectionType.APP,
                            EVcsSetupStatus.PENDING,
                            connection.getExternalWorkspaceId());
            if (accountCandidates.size() > 1) {
                List<VcsConnection> approvedCandidates = accountCandidates.stream()
                        .filter(candidate -> !stillPendingRequestIds.contains(
                                candidate.getGithubInstallationRequestId()))
                        .toList();
                if (approvedCandidates.size() != 1
                        || !Objects.equals(approvedCandidates.get(0).getId(), connection.getId())) {
                    throw new IntegrationException(
                            "Multiple CodeCrow requests target this GitHub account and approval is ambiguous. " +
                            "The connection was left pending."
                    );
                }
            }

            long requestedAccountId = Long.parseLong(connection.getExternalWorkspaceId());
            List<org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService.InstallationInfo> matches =
                    authService.listInstallations().stream()
                            .filter(installation -> installation.accountId() == requestedAccountId)
                            .toList();
            if (matches.isEmpty()) {
                log.info("GitHub request {} is no longer pending, but account {} has no approved installation yet",
                        connection.getGithubInstallationRequestId(), requestedAccountId);
                return VcsConnectionDTO.fromEntity(connection);
            }
            if (matches.size() != 1) {
                throw new IntegrationException(
                        "GitHub returned multiple installations for the exact requested account. " +
                        "The connection was left pending."
                );
            }

            var installation = matches.get(0);
            Optional<VcsConnection> existing = findGitHubAppConnectionByInstallationId(
                    installation.installationId());
            if (existing.isPresent() && !Objects.equals(existing.get().getId(), connection.getId())) {
                throw new IntegrationException(
                        "The approved GitHub App installation is already linked to another CodeCrow workspace."
                );
            }

            VcsConnection saved = activateGitHubAppConnection(
                    connection,
                    installation.installationId(),
                    installation.accountLogin(),
                    installation.accountType(),
                    authService);
            log.info("Activated request-bound GitHub installation {} for connection {}",
                    installation.installationId(), connection.getId());
            return VcsConnectionDTO.fromEntity(saved);
        } catch (NumberFormatException e) {
            throw new IntegrationException("Invalid GitHub request target on pending connection");
        } catch (GeneralSecurityException | IOException e) {
            throw new IntegrationException("Could not verify GitHub installation approval: " + e.getMessage());
        }
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
