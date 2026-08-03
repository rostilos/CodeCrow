package org.rostilos.codecrow.webserver.vcs.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.HttpAuthorizedClientFactory;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.webserver.vcs.dto.request.gitlab.GitLabCreateRequest;
import org.rostilos.codecrow.webserver.vcs.utils.BitbucketCloudConfigHandler;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class VcsConnectionWebServiceGitLabOAuthTest {

    @Mock private VcsConnectionRepository connectionRepository;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private HttpAuthorizedClientFactory httpClientFactory;
    @Mock private BitbucketCloudConfigHandler bitbucketCloudConfigHandler;
    @Mock private WorkspaceRepository workspaceRepository;
    @Mock private TokenEncryptionService tokenEncryptionService;
    @Mock private VcsClient vcsClient;

    private VcsConnectionWebService service;

    @BeforeEach
    void setUp() {
        service = new VcsConnectionWebService(
                connectionRepository,
                vcsClientProvider,
                httpClientFactory,
                bitbucketCloudConfigHandler,
                workspaceRepository,
                tokenEncryptionService);
    }

    @Test
    void oauthConnectionCannotBeReboundToAnotherGitLabInstance() {
        VcsConnection connection = gitLabConnection(
                EVcsConnectionType.APP,
                "https://gitlab.example");
        when(connectionRepository.findByWorkspace_IdAndId(7L, 17L))
                .thenReturn(Optional.of(connection));
        GitLabCreateRequest request = new GitLabCreateRequest();
        request.setBaseUrl("https://attacker.example");

        assertThatThrownBy(() -> service.updateGitLabConnection(7L, 17L, request))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("OAuth connection cannot be changed");

        verify(vcsClientProvider, never()).evictCachedClient(17L);
        verify(vcsClientProvider, never()).getClient(connection);
        verify(connectionRepository, never()).save(connection);
    }

    @Test
    void invalidOAuthIssuerIsRejectedAsARequestErrorBeforeSync() {
        VcsConnection connection = gitLabConnection(
                EVcsConnectionType.APP,
                "https://gitlab.example");
        when(connectionRepository.findByWorkspace_IdAndId(7L, 17L))
                .thenReturn(Optional.of(connection));
        GitLabCreateRequest request = new GitLabCreateRequest();
        request.setBaseUrl("ftp://gitlab.example");

        assertThatThrownBy(() -> service.updateGitLabConnection(7L, 17L, request))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("must use HTTP or HTTPS");

        verify(vcsClientProvider, never()).evictCachedClient(17L);
        verify(connectionRepository, never()).save(connection);
    }

    @Test
    void equivalentOAuthIssuerFormattingRemainsCompatible() throws Exception {
        VcsConnection connection = gitLabConnection(
                EVcsConnectionType.APP,
                "https://gitlab.example/root");
        when(connectionRepository.findByWorkspace_IdAndId(7L, 17L))
                .thenReturn(Optional.of(connection));
        when(vcsClientProvider.getClient(connection)).thenReturn(vcsClient);
        when(vcsClient.validateConnection()).thenReturn(true);
        when(connectionRepository.save(connection)).thenReturn(connection);
        GitLabCreateRequest request = new GitLabCreateRequest();
        request.setBaseUrl("HTTPS://GITLAB.EXAMPLE:443/root/api/v4/");

        VcsConnection updated = service.updateGitLabConnection(7L, 17L, request);

        assertThat(updated).isSameAs(connection);
        verify(vcsClientProvider).evictCachedClient(17L);
        verify(vcsClientProvider).getClient(connection);
    }

    @Test
    void personalTokenConnectionCanStillChangeItsCustomInstance() throws Exception {
        VcsConnection connection = gitLabConnection(
                EVcsConnectionType.PERSONAL_TOKEN,
                "https://old-gitlab.example");
        when(connectionRepository.findByWorkspace_IdAndId(7L, 17L))
                .thenReturn(Optional.of(connection));
        when(vcsClientProvider.getClient(connection)).thenReturn(vcsClient);
        when(vcsClient.validateConnection()).thenReturn(true);
        when(connectionRepository.save(connection)).thenReturn(connection);
        GitLabCreateRequest request = new GitLabCreateRequest();
        request.setBaseUrl("https://new-gitlab.example");

        VcsConnection updated = service.updateGitLabConnection(7L, 17L, request);

        assertThat(((GitLabConfig) updated.getConfiguration()).baseUrl())
                .isEqualTo("https://new-gitlab.example");
        verify(vcsClientProvider).getClient(connection);
    }

    private VcsConnection gitLabConnection(
            EVcsConnectionType connectionType,
            String baseUrl
    ) {
        VcsConnection connection = new VcsConnection();
        connection.setId(17L);
        connection.setProviderType(EVcsProvider.GITLAB);
        connection.setConnectionType(connectionType);
        connection.setConfiguration(new GitLabConfig(
                "personal-token",
                null,
                null,
                baseUrl));
        return connection;
    }
}
