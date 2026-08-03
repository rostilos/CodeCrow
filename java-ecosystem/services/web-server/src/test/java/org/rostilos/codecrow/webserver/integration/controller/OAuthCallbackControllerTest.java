package org.rostilos.codecrow.webserver.integration.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.dto.admin.BaseUrlSettingsDTO;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.webserver.exception.GitHubInstallationRecoveryException;
import org.rostilos.codecrow.webserver.integration.dto.response.VcsConnectionDTO;
import org.rostilos.codecrow.webserver.integration.service.OAuthStateService;
import org.rostilos.codecrow.webserver.integration.service.VcsIntegrationService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.springframework.http.HttpStatus;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OAuthCallbackControllerTest {

    @Mock private VcsIntegrationService integrationService;
    @Mock private OAuthStateService oAuthStateService;
    @Mock private WorkspaceService workspaceService;
    @Mock private SiteSettingsProvider siteSettingsProvider;

    private OAuthCallbackController controller;

    @BeforeEach
    void setUp() {
        org.mockito.Mockito.lenient()
                .when(siteSettingsProvider.getBaseUrlSettings()).thenReturn(
                new BaseUrlSettingsDTO(
                        "https://api.codecrow.example",
                        "https://app.codecrow.example",
                        "https://hooks.codecrow.example"));
        controller = new OAuthCallbackController(
                integrationService,
                oAuthStateService,
                workspaceService,
                siteSettingsProvider,
                new ObjectMapper());
    }

    @Test
    void githubOrganizationOwnerWithoutCodecrowStateGetsPublicResultPage() {
        var response = controller.handleGitHubCallback(
                null, null, 145918007L, "install", null, null);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.FOUND);
        assertThat(response.getHeaders().getLocation().toString())
                .isEqualTo("https://app.codecrow.example/integrations/app-installed"
                        + "?provider=github&status=installed");
        verifyNoInteractions(integrationService, oAuthStateService, workspaceService);
    }

    @Test
    void missingGitHubInstallationRedirectsIntoBoundRecoveryFlow() throws Exception {
        Workspace workspace = org.mockito.Mockito.mock(Workspace.class);
        when(workspace.getSlug()).thenReturn("acme");
        when(workspaceService.getWorkspaceById(7L)).thenReturn(workspace);
        when(oAuthStateService.validateAndExtractState("signed-state"))
                .thenReturn(new OAuthStateService.OAuthStateData(
                        EVcsProvider.GITHUB.getId(),
                        7L,
                        32L,
                        147805618L,
                        OAuthStateService.GITHUB_INSTALL_VERIFY));
        when(integrationService.handleAppCallback(
                EVcsProvider.GITHUB, "code", "signed-state", 7L))
                .thenThrow(new GitHubInstallationRecoveryException(
                        "https://github.com/apps/codecrow/installations/new"
                                + "?state=fresh-install-state",
                        new IOException("installation missing")));

        var response = controller.handleGitHubCallback(
                "code", "signed-state", null, null, null, null);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.FOUND);
        assertThat(response.getHeaders().getLocation().toString())
                .isEqualTo("https://github.com/apps/codecrow/installations/new"
                        + "?state=fresh-install-state");
    }

    @Test
    void bitbucketSuccessUsesPublicResultPage() throws Exception {
        Workspace workspace = org.mockito.Mockito.mock(Workspace.class);
        when(workspace.getSlug()).thenReturn("acme");
        when(oAuthStateService.validateAndExtractWorkspaceId("signed-state"))
                .thenReturn(7L);
        when(workspaceService.getWorkspaceById(7L)).thenReturn(workspace);
        when(integrationService.handleAppCallback(
                EVcsProvider.BITBUCKET_CLOUD, "code", "signed-state", 7L))
                .thenReturn(connected(21L, EVcsProvider.BITBUCKET_CLOUD));

        var response = controller.handleBitbucketCallback(
                "code", "signed-state", null, null);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.FOUND);
        assertThat(response.getHeaders().getLocation().toString())
                .isEqualTo("https://app.codecrow.example/integrations/app-installed"
                        + "?provider=bitbucket-cloud&status=connected"
                        + "&workspace=acme&connectionId=21");
    }

    @Test
    void gitLabSuccessUsesPublicResultPage() throws Exception {
        Workspace workspace = org.mockito.Mockito.mock(Workspace.class);
        when(workspace.getSlug()).thenReturn("acme");
        when(oAuthStateService.validateAndExtractWorkspaceId("signed-state"))
                .thenReturn(7L);
        when(workspaceService.getWorkspaceById(7L)).thenReturn(workspace);
        when(integrationService.handleAppCallback(
                EVcsProvider.GITLAB, "code", "signed-state", 7L))
                .thenReturn(connected(22L, EVcsProvider.GITLAB));

        var response = controller.handleGitLabCallback(
                "code", "signed-state", null, null);

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.FOUND);
        assertThat(response.getHeaders().getLocation().toString())
                .isEqualTo("https://app.codecrow.example/integrations/app-installed"
                        + "?provider=gitlab&status=connected"
                        + "&workspace=acme&connectionId=22");
    }

    private VcsConnectionDTO connected(Long id, EVcsProvider provider) {
        return new VcsConnectionDTO(
                id,
                provider,
                EVcsConnectionType.APP,
                "Connection",
                EVcsSetupStatus.CONNECTED,
                "external-id",
                "external-slug",
                null,
                false,
                0,
                null,
                null,
                null);
    }
}
