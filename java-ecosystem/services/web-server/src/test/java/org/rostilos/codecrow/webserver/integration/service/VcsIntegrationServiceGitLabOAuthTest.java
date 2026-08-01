package org.rostilos.codecrow.webserver.integration.service;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.dto.admin.BaseUrlSettingsDTO;
import org.rostilos.codecrow.core.dto.admin.GitLabSettingsDTO;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.ai.AiConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.BitbucketConnectInstallationRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsConnectionRepository;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.HttpAuthorizedClientFactory;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.webserver.exception.IntegrationException;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class VcsIntegrationServiceGitLabOAuthTest {

    @Mock private VcsConnectionRepository connectionRepository;
    @Mock private VcsRepoBindingRepository bindingRepository;
    @Mock private WorkspaceRepository workspaceRepository;
    @Mock private ProjectRepository projectRepository;
    @Mock private AiConnectionRepository aiConnectionRepository;
    @Mock private BitbucketConnectInstallationRepository connectInstallationRepository;
    @Mock private TokenEncryptionService encryptionService;
    @Mock private HttpAuthorizedClientFactory httpClientFactory;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private OAuthStateService oAuthStateService;
    @Mock private SiteSettingsProvider siteSettingsProvider;
    @Mock private VcsProviderCleanupService providerCleanupService;

    private VcsIntegrationService service;

    @BeforeEach
    void setUp() {
        service = new VcsIntegrationService(
                connectionRepository,
                bindingRepository,
                workspaceRepository,
                projectRepository,
                aiConnectionRepository,
                connectInstallationRepository,
                encryptionService,
                httpClientFactory,
                vcsClientProvider,
                oAuthStateService,
                siteSettingsProvider,
                providerCleanupService);
    }

    @Test
    void reconnectRejectsMismatchedIssuerBeforeGeneratingState() {
        VcsConnection connection = connection(
                EVcsConnectionType.APP,
                "https://attacker.example");
        when(connectionRepository.findById(17L)).thenReturn(Optional.of(connection));
        when(connectionRepository.findByWorkspace_IdAndId(7L, 17L))
                .thenReturn(Optional.of(connection));
        when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "https://gitlab.example"));

        assertThatThrownBy(() -> service.getReconnectUrl(7L, 17L))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("does not match the configured OAuth issuer");

        verify(oAuthStateService, never())
                .generateState(EVcsProvider.GITLAB.getId(), 7L, 17L);
    }

    @Test
    void reconnectKeepsMatchingLegacyAndConfiguredIssuerBehavior() {
        VcsConnection connection = connection(
                EVcsConnectionType.APP,
                "https://gitlab.example/root/api/v4/");
        when(connectionRepository.findById(17L)).thenReturn(Optional.of(connection));
        when(connectionRepository.findByWorkspace_IdAndId(7L, 17L))
                .thenReturn(Optional.of(connection));
        when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "HTTPS://GITLAB.EXAMPLE:443/root"));
        when(siteSettingsProvider.getBaseUrlSettings()).thenReturn(
                baseUrlSettings());
        when(oAuthStateService.generateState(
                EVcsProvider.GITLAB.getId(), 7L, 17L))
                .thenReturn("signed-state");

        var response = service.getReconnectUrl(7L, 17L);

        assertThat(response.installUrl())
                .startsWith("https://gitlab.example/root/oauth/authorize")
                .contains("client_id=client-id")
                .contains("state=signed-state");
    }

    @Test
    void callbackRejectsMismatchedIssuerBeforeSendingCredentials() throws Exception {
        VcsConnection connection = connection(
                EVcsConnectionType.APP,
                "https://attacker.example");
        when(oAuthStateService.validateAndExtractState("signed-state"))
                .thenReturn(new OAuthStateService.OAuthStateData(
                        EVcsProvider.GITLAB.getId(),
                        7L,
                        17L,
                        null,
                        null));
        when(connectionRepository.findByWorkspace_IdAndId(7L, 17L))
                .thenReturn(Optional.of(connection));
        when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                new GitLabSettingsDTO(
                        "client-id",
                        "client-secret",
                        "https://gitlab.example"));

        assertThatThrownBy(() -> service.handleAppCallback(
                EVcsProvider.GITLAB,
                "authorization-code",
                "signed-state",
                7L))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("does not match the configured OAuth issuer");

        verify(encryptionService, never()).encrypt(any());
    }

    @Test
    void successfulTokenConnectionReconnectConvertsItToOAuth() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody("""
                            {
                              "access_token": "oauth-access",
                              "refresh_token": "oauth-refresh",
                              "expires_in": 7200,
                              "scope": "api"
                            }
                            """));
            gitLab.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody("{\"id\":1}"));
            gitLab.start();

            String issuer = gitLab.url("/gitlab").toString();
            VcsConnection connection = connection(
                    EVcsConnectionType.PERSONAL_TOKEN,
                    issuer);
            when(oAuthStateService.validateAndExtractState("signed-state"))
                    .thenReturn(new OAuthStateService.OAuthStateData(
                            EVcsProvider.GITLAB.getId(),
                            7L,
                            17L,
                            null,
                            null));
            when(connectionRepository.findByWorkspace_IdAndId(7L, 17L))
                    .thenReturn(Optional.of(connection));
            when(siteSettingsProvider.getGitLabSettings()).thenReturn(
                    new GitLabSettingsDTO(
                            "client-id",
                            "client-secret",
                            issuer));
            when(siteSettingsProvider.getBaseUrlSettings()).thenReturn(
                    baseUrlSettings());
            when(workspaceRepository.findById(7L))
                    .thenReturn(Optional.of(connection.getWorkspace()));
            when(encryptionService.encrypt("oauth-access"))
                    .thenReturn("encrypted-access");
            when(encryptionService.encrypt("oauth-refresh"))
                    .thenReturn("encrypted-refresh");
            when(connectionRepository.save(connection)).thenReturn(connection);

            service.handleAppCallback(
                    EVcsProvider.GITLAB,
                    "authorization-code",
                    "signed-state",
                    7L);

            assertThat(connection.getConnectionType())
                    .isEqualTo(EVcsConnectionType.APP);
            assertThat(connection.getAccessToken()).isEqualTo("encrypted-access");
            assertThat(connection.getRefreshToken()).isEqualTo("encrypted-refresh");
            assertThat(((GitLabConfig) connection.getConfiguration()).effectiveBaseUrl())
                    .isEqualTo(gitLab.url("/gitlab").toString().replaceAll("/+$", ""));
            assertThat(gitLab.takeRequest().getPath()).isEqualTo("/gitlab/oauth/token");
            assertThat(gitLab.takeRequest().getPath()).isEqualTo("/gitlab/api/v4/user");
        }
    }

    private VcsConnection connection(
            EVcsConnectionType connectionType,
            String baseUrl
    ) {
        Workspace workspace = org.mockito.Mockito.mock(Workspace.class);
        lenient().when(workspace.getId()).thenReturn(7L);
        VcsConnection connection = new VcsConnection();
        connection.setId(17L);
        connection.setWorkspace(workspace);
        connection.setProviderType(EVcsProvider.GITLAB);
        connection.setConnectionType(connectionType);
        connection.setConfiguration(new GitLabConfig(
                connectionType == EVcsConnectionType.PERSONAL_TOKEN
                        ? "personal-token"
                        : null,
                null,
                null,
                baseUrl));
        return connection;
    }

    private BaseUrlSettingsDTO baseUrlSettings() {
        return new BaseUrlSettingsDTO(
                "https://codecrow.example",
                "https://app.codecrow.example",
                "https://hooks.codecrow.example");
    }
}
