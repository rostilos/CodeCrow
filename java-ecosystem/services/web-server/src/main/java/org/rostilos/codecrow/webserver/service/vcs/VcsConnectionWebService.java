package org.rostilos.codecrow.webserver.service.vcs;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.List;
import java.util.Optional;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.SearchBitbucketCloudReposAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.ValidateBitbucketCloudConnectionAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.response.RepositorySearchResult;
import org.rostilos.codecrow.vcsclient.bitbucket.service.VcsConnectionService;
import org.rostilos.codecrow.webserver.dto.request.vcs.bitbucket.cloud.BitbucketCloudCreateRequest;
import org.rostilos.codecrow.webserver.utils.BitbucketCloudConfigHandler;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class VcsConnectionWebService {
    private final VcsConnectionRepository vcsConnectionRepository;
    private final VcsConnectionService vcsConnectionService;
    private final BitbucketCloudConfigHandler bitbucketCloudConfigHandler;
    private final WorkspaceRepository workspaceRepository;

    public VcsConnectionWebService(
            VcsConnectionRepository vcsConnectionRepository,
            VcsConnectionService vcsConnectionService,
            BitbucketCloudConfigHandler bitbucketCloudConfigHandler,
            WorkspaceRepository workspaceRepository
    ) {
        this.vcsConnectionRepository = vcsConnectionRepository;
        this.vcsConnectionService = vcsConnectionService;
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
    ) throws GeneralSecurityException, IOException {

        Workspace ws = workspaceRepository.findById(codecrowWorkspaceId)
                .orElseThrow(() -> new IllegalArgumentException("Workspace not found"));

        var connection = new VcsConnection();
        connection.setWorkspace(ws);
        connection.setConnectionName(connectionName);
        connection.setProviderType(EVcsProvider.BITBUCKET_CLOUD);
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
    ) throws GeneralSecurityException, IOException {
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
    ) throws GeneralSecurityException, IOException {
        OkHttpClient bitbucketHttpAuthorizedClient = vcsConnectionService.getBitbucketAuthorizedClient(codecrowWorkspaceId, vcsConnection.getId());

        ValidateBitbucketCloudConnectionAction validateBitbucketCloudConnectionAction =
                new ValidateBitbucketCloudConnectionAction(bitbucketHttpAuthorizedClient);
        boolean isConnectionValid = validateBitbucketCloudConnectionAction.isConnectionValid();
        vcsConnection.setSetupStatus(isConnectionValid ? EVcsSetupStatus.CONNECTED : EVcsSetupStatus.ERROR);

        SearchBitbucketCloudReposAction searchBitbucketCloudReposAction =
                new SearchBitbucketCloudReposAction(bitbucketHttpAuthorizedClient);
        int repositoriesCount = searchBitbucketCloudReposAction.getRepositoriesCount(bitbucketCloudConfig.workspaceId());
        vcsConnection.setRepoCount(repositoriesCount);
        return vcsConnection;
    }

    public RepositorySearchResult searchBitbucketCloudRepositories(Long workspaceId, Long connectionId, String query, int page) throws IOException, GeneralSecurityException {
        OkHttpClient client = vcsConnectionService.getBitbucketAuthorizedClient(workspaceId, connectionId);
        SearchBitbucketCloudReposAction search = new SearchBitbucketCloudReposAction(client);
        VcsConnection connection = vcsConnectionRepository.findByWorkspace_IdAndId(workspaceId, connectionId)
                .orElseThrow(() -> new IllegalArgumentException("VCS connection not found"));
        BitbucketCloudConfig config = (BitbucketCloudConfig) connection.getConfiguration();

        if (query == null || query.isBlank()) {
            return search.getRepositories(config.workspaceId(), page);
        } else {
            return search.searchRepositories(config.workspaceId(), query, page);
        }
    }
}
