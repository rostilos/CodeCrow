package org.rostilos.codecrow.webserver.integration.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
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
import org.rostilos.codecrow.webserver.exception.IntegrationException;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
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
                siteSettingsProvider
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
        verify(connectionRepository, never()).findByProviderTypeAndInstallationId(
                EVcsProvider.GITHUB, "145918007");
        verify(connectionRepository, never()).findByProviderTypeAndExternalWorkspaceId(
                EVcsProvider.GITHUB, "145918007");
        verifyNoInteractions(siteSettingsProvider, encryptionService);
    }

    @Test
    @DisplayName("signed installation webhook cannot select an arbitrary pending workspace")
    void webhookDoesNotClaimUnassociatedInstallation() {
        when(connectionRepository.findByProviderTypeAndInstallationId(
                EVcsProvider.GITHUB, "145918007")).thenReturn(Optional.empty());
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
        when(connectionRepository.findByProviderTypeAndInstallationId(
                EVcsProvider.GITHUB, "145918007")).thenReturn(Optional.of(pending));

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
