package org.rostilos.codecrow.webserver.integration.controller;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.dto.admin.BaseUrlSettingsDTO;
import org.rostilos.codecrow.core.model.vcs.BitbucketConnectInstallation;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.webserver.integration.dto.response.VcsConnectionDTO;
import org.rostilos.codecrow.webserver.integration.service.BitbucketConnectService;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BitbucketConnectControllerTest {

    @Mock private BitbucketConnectService connectService;
    @Mock private SiteSettingsProvider siteSettingsProvider;
    @Mock private WorkspaceMemberRepository workspaceMemberRepository;

    @Test
    void ownerApprovalReturnsToPublicResultUsingCamelCaseClientKey() throws Exception {
        when(connectService.reconnectExistingWorkspaceInstallation(10L))
                .thenReturn(Optional.empty());
        when(siteSettingsProvider.getBaseUrlSettings()).thenReturn(
                new BaseUrlSettingsDTO(
                        "https://api.codecrow.example",
                        "https://app.codecrow.example",
                        "https://hooks.codecrow.example"));

        BitbucketConnectController controller = new BitbucketConnectController(
                connectService, siteSettingsProvider, workspaceMemberRepository);
        var start = controller.startInstall(10L, "acme");
        String state = (String) start.getBody().get("state");

        BitbucketConnectInstallation installation = new BitbucketConnectInstallation();
        installation.setId(44L);
        installation.setClientKey("client-key-44");
        installation.setBitbucketWorkspaceSlug("acme-bb");
        when(connectService.findByClientKey("client-key-44"))
                .thenReturn(Optional.of(installation));
        when(connectService.linkToCodecrowWorkspace(44L, 10L))
                .thenReturn(new VcsConnectionDTO(
                        71L,
                        EVcsProvider.BITBUCKET_CLOUD,
                        EVcsConnectionType.APP,
                        "Bitbucket – acme-bb",
                        EVcsSetupStatus.CONNECTED,
                        "{workspace-uuid}",
                        "acme-bb",
                        false,
                        0,
                        null,
                        null,
                        null));

        var result = controller.completeInstall(
                state,
                null,
                "client-key-44",
                null,
                null);

        assertThat(result.getStatusCode().value()).isEqualTo(302);
        assertThat(result.getHeaders().getLocation().toString())
                .isEqualTo("https://app.codecrow.example/integrations/app-installed"
                        + "?provider=bitbucket-cloud&status=connected"
                        + "&workspace=acme&connectionId=71");
        verify(connectService).linkToCodecrowWorkspace(44L, 10L);
    }
}
