package org.rostilos.codecrow.webserver.service.vcs;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.List;
import java.util.Optional;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.SearchBitbucketCloudReposAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.ValidateBitbucketCloudConnectionAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.response.RepositorySearchResult;
import org.rostilos.codecrow.webserver.dto.request.vcs.bitbucket.cloud.BitbucketCloudCreateRequest;
import org.rostilos.codecrow.webserver.utils.BitbucketCloudConfigHandler;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class VcsConnectionWebService {
    private final VcsConnectionRepository vcsConnectionRepository;
    @SuppressWarnings("unused")
    private final VcsClientProvider vcsClientProvider;
    private final BitbucketCloudConfigHandler bitbucketCloudConfigHandler;
    private final WorkspaceRepository workspaceRepository;

    public VcsConnectionWebService(
            VcsConnectionRepository vcsConnectionRepository,
            VcsClientProvider vcsClientProvider,
            BitbucketCloudConfigHandler bitbucketCloudConfigHandler,
            WorkspaceRepository workspaceRepository
    ) {
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.vcsClientProvider = vcsClientProvider;
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
     * Get external workspace ID from connection - supports both APP and OAUTH_MANUAL connection types.
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
}
