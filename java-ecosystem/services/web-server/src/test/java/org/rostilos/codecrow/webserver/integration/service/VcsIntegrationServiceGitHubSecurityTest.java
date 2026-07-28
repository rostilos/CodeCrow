package org.rostilos.codecrow.webserver.integration.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.dto.admin.BaseUrlSettingsDTO;
import org.rostilos.codecrow.core.dto.admin.GitHubSettingsDTO;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
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
import org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService;
import org.rostilos.codecrow.webserver.exception.IntegrationException;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("VcsIntegrationService GitHub tenant isolation")
class VcsIntegrationServiceGitHubSecurityTest {

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
                providerCleanupService
        );
    }

    @Test
    @DisplayName("sync never claims a globally visible installation for an unassociated pending connection")
    void pendingSyncDoesNotDiscoverOrClaimGlobalInstallations() {
        VcsConnection pending = pendingConnection(34L, null);
        when(connectionRepository.findByWorkspace_IdAndId(10L, 34L)).thenReturn(Optional.of(pending));

        var result = service.syncConnection(10L, 34L);

        assertThat(result.status()).isEqualTo(EVcsSetupStatus.PENDING);
        assertThat(result.externalWorkspaceId()).isNull();
        verify(connectionRepository, never())
                .findByProviderTypeAndConnectionTypeAndSetupStatus(
                        EVcsProvider.GITHUB, EVcsConnectionType.APP, EVcsSetupStatus.PENDING);
        verify(connectionRepository, never()).save(pending);
        verifyNoInteractions(siteSettingsProvider, encryptionService);
    }

    @Test
    @DisplayName("spoofable setup callback does not persist or reserve an installation ID")
    void setupCallbackKeepsInstallationIdOutOfDatabaseUntilVerification() {
        Workspace workspace = org.mockito.Mockito.mock(Workspace.class);
        when(workspaceRepository.findById(10L)).thenReturn(Optional.of(workspace));
        when(connectionRepository.save(any(VcsConnection.class))).thenAnswer(invocation -> {
            VcsConnection saved = invocation.getArgument(0);
            saved.setId(34L);
            return saved;
        });

        var result = service.handleGitHubAppInstallation(145918007L, 10L, null);

        assertThat(result.id()).isEqualTo(34L);
        assertThat(result.status()).isEqualTo(EVcsSetupStatus.PENDING);
        assertThat(result.externalWorkspaceId()).isNull();
        verify(connectionRepository, never()).findAllByProviderTypeAndInstallationId(
                EVcsProvider.GITHUB, "145918007");
        verify(connectionRepository, never()).findByProviderTypeAndExternalWorkspaceId(
                EVcsProvider.GITHUB, "145918007");
        verifyNoInteractions(siteSettingsProvider, encryptionService);
    }

    @Test
    @DisplayName("signed installation webhook cannot select an arbitrary pending workspace")
    void webhookDoesNotClaimUnassociatedInstallation() {
        when(connectionRepository.findAllByProviderTypeAndInstallationId(
                EVcsProvider.GITHUB, "145918007")).thenReturn(java.util.List.of());
        when(connectionRepository.findByProviderTypeAndExternalWorkspaceId(
                EVcsProvider.GITHUB, "145918007")).thenReturn(java.util.List.of());

        assertThatThrownBy(() -> service.completeGitHubAppInstallation(
                145918007L, 100L, "AndraGroup", "Organization"))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("not associated with a CodeCrow workspace");

        verify(connectionRepository, never()).save(org.mockito.ArgumentMatchers.any());
        verifyNoInteractions(siteSettingsProvider, encryptionService);
    }

    @Test
    @DisplayName("webhook cannot activate a legacy pre-associated row without a request binding")
    void webhookDoesNotBypassRequestBinding() {
        VcsConnection pending = pendingConnection(34L, "145918007");
        when(connectionRepository.findAllByProviderTypeAndInstallationId(
                EVcsProvider.GITHUB, "145918007")).thenReturn(java.util.List.of(pending));

        assertThatThrownBy(() -> service.completeGitHubAppInstallation(
                145918007L, 100L, "AndraGroup", "Organization"))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("verification is required");

        verify(connectionRepository, never()).save(org.mockito.ArgumentMatchers.any());
        verifyNoInteractions(siteSettingsProvider, encryptionService);
    }

    @Test
    @DisplayName("workspace token refresh cannot bypass verification for a pending installation")
    void pendingConnectionCannotMintInstallationToken() {
        Workspace workspace = org.mockito.Mockito.mock(Workspace.class);
        when(workspace.getId()).thenReturn(10L);
        VcsConnection pending = pendingConnection(34L, "145918007");
        pending.setWorkspace(workspace);
        when(connectionRepository.findById(34L)).thenReturn(Optional.of(pending));

        assertThatThrownBy(() -> service.refreshConnectionToken(10L, 34L))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("pending GitHub App installation");

        verifyNoInteractions(siteSettingsProvider, encryptionService);
    }

    @Test
    @DisplayName("errored App reconnect verifies its exact installation instead of starting another install")
    void erroredAppReconnectDoesNotStartDuplicateInstallation() {
        Workspace workspace = org.mockito.Mockito.mock(Workspace.class);
        when(workspace.getId()).thenReturn(10L);
        VcsConnection connection = pendingConnection(34L, "145918007");
        connection.setWorkspace(workspace);
        connection.setSetupStatus(EVcsSetupStatus.ERROR);
        when(connectionRepository.findById(34L)).thenReturn(Optional.of(connection));
        when(connectionRepository.findByWorkspace_IdAndId(10L, 34L))
                .thenReturn(Optional.of(connection));
        when(siteSettingsProvider.getGitHubSettings()).thenReturn(new GitHubSettingsDTO(
                "12345",
                null,
                "configured-private-key",
                "webhook-secret",
                "codecrow",
                "oauth-client-id",
                "oauth-client-secret"
        ));
        when(siteSettingsProvider.getBaseUrlSettings()).thenReturn(new BaseUrlSettingsDTO(
                "https://api.codecrow.example",
                "https://codecrow.example",
                "https://api.codecrow.example"
        ));
        when(oAuthStateService.generateState(
                eq(EVcsProvider.GITHUB.getId()),
                eq(10L),
                eq(34L),
                eq(145918007L),
                eq(OAuthStateService.GITHUB_INSTALL_VERIFY)))
                .thenReturn("signed-verification-state");

        var result = service.getReconnectUrl(10L, 34L);

        assertThat(result.installUrl())
                .startsWith("https://github.com/login/oauth/authorize")
                .contains("state=signed-verification-state")
                .doesNotContain("/installations/new");
        verify(oAuthStateService).generateState(
                EVcsProvider.GITHUB.getId(),
                10L,
                34L,
                145918007L,
                OAuthStateService.GITHUB_INSTALL_VERIFY);
    }

    @Test
    @DisplayName("missing App installation continues a fresh install on the same connection")
    void missingInstallationReusesErroredConnectionForFreshInstall() throws Exception {
        Workspace workspace = mock(Workspace.class);
        when(workspaceRepository.findById(10L)).thenReturn(Optional.of(workspace));

        VcsConnection connection = pendingConnection(34L, "145918007");
        connection.setWorkspace(workspace);
        connection.setSetupStatus(EVcsSetupStatus.ERROR);
        connection.setAccessToken("stale-token");
        when(connectionRepository.findByWorkspace_IdAndId(10L, 34L))
                .thenReturn(Optional.of(connection));
        when(connectionRepository.save(connection)).thenReturn(connection);

        GitHubAppAuthService authService = mock(GitHubAppAuthService.class);
        when(authService.getAuthenticatedUser("user-token"))
                .thenReturn(new GitHubAppAuthService.UserInfo(42L, "octocat"));
        when(authService.listInstallationsForUser("user-token"))
                .thenReturn(java.util.List.of());
        when(authService.listInstallationRequests()).thenReturn(java.util.List.of());
        when(siteSettingsProvider.getGitHubSettings()).thenReturn(new GitHubSettingsDTO(
                "12345",
                null,
                "configured-private-key",
                "webhook-secret",
                "codecrow",
                "oauth-client-id",
                "oauth-client-secret"
        ));
        when(oAuthStateService.generateState(
                EVcsProvider.GITHUB.getId(),
                10L,
                34L,
                null,
                OAuthStateService.GITHUB_INSTALL_SELECT))
                .thenReturn("fresh-install-state");

        var result = service.continueGitHubAppInstallation(
                "user-token", 10L, 34L, "verification-state", authService);

        assertThat(result.installUrl())
                .isEqualTo("https://github.com/apps/codecrow/installations/new"
                        + "?state=fresh-install-state");
        assertThat(connection.getSetupStatus()).isEqualTo(EVcsSetupStatus.PENDING);
        assertThat(connection.getInstallationId()).isNull();
        assertThat(connection.getExternalWorkspaceId()).isNull();
        assertThat(connection.getAccessToken()).isNull();
        verify(connectionRepository).save(connection);
    }

    @Test
    @DisplayName("an account-level installation already used by another workspace is reusable")
    void sharedInstallationReconnectsWithoutReturningToGitHubConfigure() throws Exception {
        Workspace workspace = mock(Workspace.class);
        when(workspace.getSlug()).thenReturn("acme");
        when(workspaceRepository.findById(10L)).thenReturn(Optional.of(workspace));

        VcsConnection connection = pendingConnection(34L, null);
        connection.setWorkspace(workspace);
        connection.setSetupStatus(EVcsSetupStatus.PENDING);
        when(connectionRepository.findByWorkspace_IdAndId(10L, 34L))
                .thenReturn(Optional.of(connection));
        when(connectionRepository.save(connection)).thenReturn(connection);

        GitHubAppAuthService authService = mock(GitHubAppAuthService.class);
        var installation = new GitHubAppAuthService.InstallationInfo(
                149447866L,
                42L,
                "octocat",
                "User",
                null,
                "User");
        when(authService.getAuthenticatedUser("user-token"))
                .thenReturn(new GitHubAppAuthService.UserInfo(42L, "octocat"));
        when(authService.listInstallationsForUser("user-token"))
                .thenReturn(java.util.List.of(installation));
        when(authService.listInstallationRequests()).thenReturn(java.util.List.of());
        when(authService.getInstallationAccessToken(149447866L))
                .thenReturn(new GitHubAppAuthService.InstallationToken(
                        "installation-token",
                        java.time.LocalDateTime.now().plusHours(1)));
        when(encryptionService.encrypt("installation-token"))
                .thenReturn("encrypted-installation-token");
        when(siteSettingsProvider.getBaseUrlSettings()).thenReturn(new BaseUrlSettingsDTO(
                "https://api.codecrow.example",
                "https://codecrow.example",
                "https://api.codecrow.example"
        ));

        var result = service.continueGitHubAppInstallation(
                "user-token", 10L, 34L, "verification-state", authService);

        assertThat(result.installUrl())
                .isEqualTo("https://codecrow.example/integrations/app-installed"
                        + "?provider=github&status=connected&workspace=acme&connectionId=34");
        assertThat(result.installUrl()).doesNotContain("github.com");
        assertThat(connection.getSetupStatus()).isEqualTo(EVcsSetupStatus.CONNECTED);
        assertThat(connection.getInstallationId()).isEqualTo("149447866");
        assertThat(connection.getExternalWorkspaceId()).isEqualTo("42");
        assertThat(connection.getExternalWorkspaceSlug()).isEqualTo("octocat");
    }

    @Test
    @DisplayName("multiple reusable installations return to the GitHub settings tab for selection")
    void multipleInstallationsOpenGitHubCandidateSelection() throws Exception {
        Workspace workspace = mock(Workspace.class);
        when(workspace.getSlug()).thenReturn("acme");
        when(workspaceRepository.findById(10L)).thenReturn(Optional.of(workspace));

        VcsConnection connection = pendingConnection(32L, null);
        connection.setWorkspace(workspace);
        connection.setSetupStatus(EVcsSetupStatus.PENDING);
        when(connectionRepository.findByWorkspace_IdAndId(10L, 32L))
                .thenReturn(Optional.of(connection));
        when(connectionRepository.save(connection)).thenReturn(connection);

        GitHubAppAuthService authService = mock(GitHubAppAuthService.class);
        var personalInstallation = new GitHubAppAuthService.InstallationInfo(
                149447866L, 42L, "octocat", "User", null, "User");
        var organizationInstallation = new GitHubAppAuthService.InstallationInfo(
                149455110L, 99L, "acme-inc", "Organization", null, "Organization");
        when(authService.getAuthenticatedUser("user-token"))
                .thenReturn(new GitHubAppAuthService.UserInfo(42L, "octocat"));
        when(authService.listInstallationsForUser("user-token"))
                .thenReturn(java.util.List.of(personalInstallation, organizationInstallation));
        when(authService.listInstallationRequests()).thenReturn(java.util.List.of());
        when(siteSettingsProvider.getBaseUrlSettings()).thenReturn(new BaseUrlSettingsDTO(
                "https://api.codecrow.example",
                "https://codecrow.example",
                "https://api.codecrow.example"
        ));

        var result = service.continueGitHubAppInstallation(
                "user-token", 10L, 32L, "verification-state", authService);

        assertThat(result.installUrl())
                .isEqualTo("https://codecrow.example/dashboard/acme/hosting"
                        + "?tab=github&provider=github&existingInstallations=true&connectionId=32");
        assertThat(connection.getGithubInstallationCandidates())
                .isEqualTo("149447866,149455110");
        assertThat(connection.getSetupStatus()).isEqualTo(EVcsSetupStatus.PENDING);
    }

    @Test
    @DisplayName("connection deletion removes provider authorization before the local retry row")
    void deleteConnectionCleansProviderBeforeDeletingLocalRow() {
        VcsConnection connection = pendingConnection(34L, null);
        when(connectionRepository.findByWorkspace_IdAndId(10L, 34L))
                .thenReturn(Optional.of(connection));
        when(bindingRepository.findByVcsConnection_Id(34L)).thenReturn(java.util.List.of());
        when(connectInstallationRepository.findByVcsConnection_Id(34L))
                .thenReturn(Optional.empty());

        service.deleteConnection(10L, 34L);

        var order = org.mockito.Mockito.inOrder(
                providerCleanupService, connectionRepository);
        order.verify(providerCleanupService).removeProviderAuthorization(connection);
        order.verify(connectionRepository).delete(connection);
    }

    @Test
    @DisplayName("provider cleanup failure keeps the local connection for retry")
    void deleteConnectionKeepsLocalRowWhenProviderCleanupFails() {
        VcsConnection connection = pendingConnection(34L, null);
        when(connectionRepository.findByWorkspace_IdAndId(10L, 34L))
                .thenReturn(Optional.of(connection));
        when(bindingRepository.findByVcsConnection_Id(34L)).thenReturn(java.util.List.of());
        doThrow(new IntegrationException("provider unavailable"))
                .when(providerCleanupService).removeProviderAuthorization(connection);

        assertThatThrownBy(() -> service.deleteConnection(10L, 34L))
                .isInstanceOf(IntegrationException.class)
                .hasMessageContaining("provider unavailable");

        verify(connectionRepository, never()).delete(any());
        verifyNoInteractions(connectInstallationRepository);
    }

    private VcsConnection pendingConnection(long id, String installationId) {
        VcsConnection connection = new VcsConnection();
        connection.setId(id);
        connection.setProviderType(EVcsProvider.GITHUB);
        connection.setConnectionType(EVcsConnectionType.APP);
        connection.setSetupStatus(EVcsSetupStatus.PENDING);
        connection.setConnectionName("GitHub – Pending Verification");
        connection.setInstallationId(installationId);
        connection.setExternalWorkspaceId(installationId);
        return connection;
    }
}
