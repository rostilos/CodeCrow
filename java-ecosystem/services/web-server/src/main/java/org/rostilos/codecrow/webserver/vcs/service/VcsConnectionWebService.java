package org.rostilos.codecrow.webserver.vcs.service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.Optional;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.HttpAuthorizedClientFactory;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.SearchBitbucketCloudReposAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.ValidateBitbucketCloudConnectionAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.response.RepositorySearchResult;
import org.rostilos.codecrow.vcsclient.github.actions.SearchRepositoriesAction;
import org.rostilos.codecrow.vcsclient.github.actions.ValidateConnectionAction;
import org.rostilos.codecrow.webserver.vcs.dto.request.cloud.BitbucketCloudCreateRequest;
import org.rostilos.codecrow.webserver.vcs.dto.request.github.GitHubCreateRequest;
import org.rostilos.codecrow.webserver.vcs.utils.BitbucketCloudConfigHandler;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class VcsConnectionWebService {
    private final VcsConnectionRepository vcsConnectionRepository;
    private final VcsClientProvider vcsClientProvider;
    private final HttpAuthorizedClientFactory httpClientFactory;
    private final BitbucketCloudConfigHandler bitbucketCloudConfigHandler;
    private final WorkspaceRepository workspaceRepository;

    public VcsConnectionWebService(
            VcsConnectionRepository vcsConnectionRepository,
            VcsClientProvider vcsClientProvider,
            HttpAuthorizedClientFactory httpClientFactory,
            BitbucketCloudConfigHandler bitbucketCloudConfigHandler,
            WorkspaceRepository workspaceRepository
    ) {
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.httpClientFactory = httpClientFactory;
        this.bitbucketCloudConfigHandler = bitbucketCloudConfigHandler;
        this.workspaceRepository = workspaceRepository;
    }

    @Transactional
    public List<VcsConnection> getWorkspaceBitbucketCloudConnections(Long workspaceId) {
        List<VcsConnection> userBitbucketConnections = vcsConnectionRepository.findByWorkspace_IdAndProviderType(workspaceId, EVcsProvider.BITBUCKET_CLOUD);
        if (userBitbucketConnections == null) {
            userBitbucketConnections = List.of();
        }
        return userBitbucketConnections;
    }

    @Transactional
    public VcsConnection createBitbucketCloudConnection(
            Long codecrowWorkspaceId,
            BitbucketCloudConfig bitbucketCloudConfig,
            String connectionName
    ) {

        Workspace ws = workspaceRepository.findById(codecrowWorkspaceId)
                .orElseThrow(() -> new IllegalArgumentException("Workspace not found"));

        var connection = new VcsConnection();
        connection.setWorkspace(ws);
        connection.setConnectionName(connectionName);
        connection.setProviderType(EVcsProvider.BITBUCKET_CLOUD);
        connection.setConnectionType(EVcsConnectionType.OAUTH_MANUAL);
        connection.setConfiguration(bitbucketCloudConfig);
        connection.setSetupStatus(EVcsSetupStatus.PENDING);
        VcsConnection createdConnection = vcsConnectionRepository.save(connection);
        VcsConnection updatedConnection = syncConnectionInfo(createdConnection, codecrowWorkspaceId, bitbucketCloudConfig);

        return vcsConnectionRepository.save(updatedConnection);
    }

    @Transactional
    public VcsConnection updateBitbucketCloudConnection(
            Long codecrowWorkspaceId,
            Long connectionId,
            BitbucketCloudCreateRequest request
    ) throws GeneralSecurityException {
        VcsConnection connection = vcsConnectionRepository.findByWorkspace_IdAndId(codecrowWorkspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException("Connection not found"));

        BitbucketCloudConfig  bitbucketCloudConfig = (BitbucketCloudConfig) connection.getConfiguration();
        BitbucketCloudConfig updatedCloudConfig = bitbucketCloudConfigHandler.updateBitbucketConfigFromRequest(bitbucketCloudConfig, request);
        connection.setConfiguration(updatedCloudConfig);
        String updatedName = request.getConnectionName() != null ? request.getConnectionName() : connection.getConnectionName();
        connection.setConnectionName(updatedName);
        VcsConnection updatedConnection = syncConnectionInfo(connection, codecrowWorkspaceId, updatedCloudConfig);
        return vcsConnectionRepository.save(updatedConnection);
    }


    @Transactional
    public void deleteBitbucketCloudConnection(Long workspaceId, Long connId, EVcsProvider provider) {
        VcsConnection existing = getOwnedGitConnection(workspaceId, connId, provider);
        if (existing == null) {
            throw new IllegalArgumentException("Connection not found or not owned by user");
        }
        vcsConnectionRepository.delete(existing);
    }

    public VcsConnection getOwnedGitConnection(Long workspaceId, Long connId, EVcsProvider provider) {
        Optional<VcsConnection> opt = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, connId);
        if (opt.isEmpty()) return null;
        VcsConnection gc = opt.get();
        if (provider != null && gc.getProviderType() != provider) return null;
        return gc;
    }

    private VcsConnection syncConnectionInfo(
            VcsConnection vcsConnection,
            Long codecrowWorkspaceId,
            BitbucketCloudConfig bitbucketCloudConfig
    ) {
        try {
            // Use unified VcsClientProvider to get authorized HTTP client
            OkHttpClient bitbucketHttpAuthorizedClient = vcsClientProvider.getHttpClient(vcsConnection);

            ValidateBitbucketCloudConnectionAction validateBitbucketCloudConnectionAction =
                    new ValidateBitbucketCloudConnectionAction(bitbucketHttpAuthorizedClient);
            boolean isConnectionValid = validateBitbucketCloudConnectionAction.isConnectionValid();
            vcsConnection.setSetupStatus(isConnectionValid ? EVcsSetupStatus.CONNECTED : EVcsSetupStatus.ERROR);

            SearchBitbucketCloudReposAction searchBitbucketCloudReposAction =
                    new SearchBitbucketCloudReposAction(bitbucketHttpAuthorizedClient);
            int repositoriesCount = searchBitbucketCloudReposAction.getRepositoriesCount(bitbucketCloudConfig.workspaceId());
            vcsConnection.setRepoCount(repositoriesCount);
        } catch (IOException e) {
            vcsConnection.setSetupStatus(EVcsSetupStatus.ERROR);
        }
        return vcsConnection;
    }

    public RepositorySearchResult searchBitbucketCloudRepositories(Long workspaceId, Long connectionId, String query, int page) throws IOException {
        VcsConnection connection = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException("VCS connection not found"));
        
        // Use unified VcsClientProvider to get authorized HTTP client
        OkHttpClient client = vcsClientProvider.getHttpClient(connection);
        SearchBitbucketCloudReposAction search = new SearchBitbucketCloudReposAction(client);
        
        // Get workspace ID from either config or connection fields
        String externalWorkspaceId = getExternalWorkspaceId(connection);

        if (query == null || query.isBlank()) {
            return search.getRepositories(externalWorkspaceId, page);
        } else {
            return search.searchRepositories(externalWorkspaceId, query, page);
        }
    }
    
    /**
     * Get external workspace ID from connection - supports APP and OAUTH_MANUAL connection types.
     */
    private String getExternalWorkspaceId(VcsConnection connection) {
        // For APP connections, use the stored external workspace slug/id
        if (connection.getConnectionType() == EVcsConnectionType.APP) {
            return connection.getExternalWorkspaceSlug() != null 
                    ? connection.getExternalWorkspaceSlug() 
                    : connection.getExternalWorkspaceId();
        }
        
        // For OAUTH_MANUAL connections, get from config
        if (connection.getConfiguration() instanceof BitbucketCloudConfig config) {
            return config.workspaceId();
        }
        
        // Fallback to stored values
        return connection.getExternalWorkspaceSlug() != null 
                ? connection.getExternalWorkspaceSlug() 
                : connection.getExternalWorkspaceId();
    }

    // ========== GitHub Methods ==========

    @Transactional
    public List<VcsConnection> getWorkspaceGitHubConnections(Long workspaceId) {
        List<VcsConnection> connections = vcsConnectionRepository.findByWorkspace_IdAndProviderType(workspaceId, EVcsProvider.GITHUB);
        return connections != null ? connections : List.of();
    }

    @Transactional
    public VcsConnection createGitHubConnection(
            Long codecrowWorkspaceId,
            GitHubConfig gitHubConfig,
            String connectionName
    ) {
        Workspace ws = workspaceRepository.findById(codecrowWorkspaceId)
                .orElseThrow(() -> new IllegalArgumentException("Workspace not found"));

        var connection = new VcsConnection();
        connection.setWorkspace(ws);
        connection.setConnectionName(connectionName);
        connection.setProviderType(EVcsProvider.GITHUB);
        connection.setConnectionType(EVcsConnectionType.PERSONAL_TOKEN);
        connection.setConfiguration(gitHubConfig);
        connection.setSetupStatus(EVcsSetupStatus.PENDING);
        connection.setExternalWorkspaceSlug(gitHubConfig.organizationId());
        connection.setExternalWorkspaceId(gitHubConfig.organizationId());
        
        VcsConnection createdConnection = vcsConnectionRepository.save(connection);
        VcsConnection updatedConnection = syncGitHubConnectionInfo(createdConnection, gitHubConfig);

        return vcsConnectionRepository.save(updatedConnection);
    }

    @Transactional
    public VcsConnection updateGitHubConnection(
            Long codecrowWorkspaceId,
            Long connectionId,
            GitHubCreateRequest request
    ) {
        VcsConnection connection = vcsConnectionRepository.findByWorkspace_IdAndId(codecrowWorkspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException("Connection not found"));

        if (connection.getProviderType() != EVcsProvider.GITHUB) {
            throw new IllegalArgumentException("Not a GitHub connection");
        }

        GitHubConfig currentConfig = connection.getConfiguration() instanceof GitHubConfig 
                ? (GitHubConfig) connection.getConfiguration() 
                : null;
        
        GitHubConfig updatedConfig = new GitHubConfig(
                request.getAccessToken() != null ? request.getAccessToken() : 
                        (currentConfig != null ? currentConfig.accessToken() : null),
                request.getOrganizationId() != null ? request.getOrganizationId() : 
                        (currentConfig != null ? currentConfig.organizationId() : null),
                currentConfig != null ? currentConfig.allowedRepos() : null
        );
        
        connection.setConfiguration(updatedConfig);
        connection.setExternalWorkspaceSlug(updatedConfig.organizationId());
        connection.setExternalWorkspaceId(updatedConfig.organizationId());
        
        if (request.getConnectionName() != null) {
            connection.setConnectionName(request.getConnectionName());
        }
        
        VcsConnection updatedConnection = syncGitHubConnectionInfo(connection, updatedConfig);
        return vcsConnectionRepository.save(updatedConnection);
    }

    @Transactional
    public void deleteGitHubConnection(Long workspaceId, Long connId) {
        VcsConnection existing = getOwnedGitConnection(workspaceId, connId, EVcsProvider.GITHUB);
        if (existing == null) {
            throw new NoSuchElementException("Connection not found or not owned by workspace");
        }
        vcsConnectionRepository.delete(existing);
    }

    private VcsConnection syncGitHubConnectionInfo(VcsConnection vcsConnection, GitHubConfig gitHubConfig) {
        try {
            OkHttpClient httpClient = httpClientFactory.createGitHubClient(gitHubConfig.accessToken());

            ValidateConnectionAction validateAction = new ValidateConnectionAction(httpClient);
            boolean isConnectionValid = validateAction.isConnectionValid();
            vcsConnection.setSetupStatus(isConnectionValid ? EVcsSetupStatus.CONNECTED : EVcsSetupStatus.ERROR);

            if (isConnectionValid && gitHubConfig.organizationId() != null) {
                SearchRepositoriesAction searchAction = new SearchRepositoriesAction(httpClient);
                int repositoriesCount = searchAction.getRepositoriesCount(gitHubConfig.organizationId());
                vcsConnection.setRepoCount(repositoriesCount);
            }
        } catch (IOException e) {
            vcsConnection.setSetupStatus(EVcsSetupStatus.ERROR);
        }
        return vcsConnection;
    }

    public org.rostilos.codecrow.vcsclient.github.dto.response.RepositorySearchResult searchGitHubRepositories(
            Long workspaceId, 
            Long connectionId, 
            String query, 
            int page
    ) throws IOException {
        VcsConnection connection = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException("VCS connection not found"));
        
        if (connection.getProviderType() != EVcsProvider.GITHUB) {
            throw new IllegalArgumentException("Not a GitHub connection");
        }
        
        OkHttpClient client = vcsClientProvider.getHttpClient(connection);
        SearchRepositoriesAction search = new SearchRepositoriesAction(client);
        
        String owner = getExternalWorkspaceId(connection);

        if (query == null || query.isBlank()) {
            return search.getRepositories(owner, page);
        } else {
            return search.searchRepositories(owner, query, page);
        }
    }
}
