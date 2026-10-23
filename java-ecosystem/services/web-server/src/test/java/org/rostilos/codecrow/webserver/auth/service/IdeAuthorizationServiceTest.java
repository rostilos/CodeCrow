package org.rostilos.codecrow.webserver.auth.service;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.rostilos.codecrow.core.model.user.IdeAuthorization;
import org.rostilos.codecrow.core.model.user.PersonalAccessToken;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.user.IdeAuthorizationRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.service.PersonalAccessTokenService;
import org.springframework.web.server.ResponseStatusException;
import java.time.Instant;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class IdeAuthorizationServiceTest {
    private final IdeAuthorizationRepository requests = mock(IdeAuthorizationRepository.class);
    private final WorkspaceRepository workspaces = mock(WorkspaceRepository.class);
    private final PersonalAccessTokenService tokens = mock(PersonalAccessTokenService.class);
    private final IdeAuthorizationService service = new IdeAuthorizationService(requests, workspaces, tokens);

    @Test
    void browserApprovalBindsTokenToSignedInMemberAndCannotBeReplayed() {
        var start = service.start("team");
        var captor = ArgumentCaptor.forClass(IdeAuthorization.class);
        verify(requests).save(captor.capture());
        var grant = captor.getValue();
        var secret = (String) start.get("deviceCode");
        assertThat(grant.getDeviceHash()).isEqualTo(PersonalAccessTokenService.hash(secret));
        assertThat(grant.getDeviceHash()).doesNotContain(secret);
        when(requests.findByDeviceHash(grant.getDeviceHash())).thenReturn(Optional.of(grant));
        when(requests.findForApproval(grant.getUserCode())).thenReturn(Optional.of(grant));
        var workspace = mock(Workspace.class);
        when(workspace.getId()).thenReturn(10L);
        when(workspaces.findBySlug("team")).thenReturn(Optional.of(workspace));
        assertThat(service.poll(secret).get("status")).isEqualTo("pending");
        assertThatThrownBy(() -> service.approve(null, grant.getUserCode(), "team", true, false, 90)).isInstanceOf(ResponseStatusException.class);
        assertThatThrownBy(() -> service.approve(5L, grant.getUserCode(), "other", true, false, 90)).isInstanceOf(ResponseStatusException.class);
        verifyNoInteractions(tokens);
        service.approve(5L, grant.getUserCode(), "team", true, false, 90);
        verify(tokens).create(5L, 10L, "IDE connection", false, 90, grant.getDeviceHash());
        when(tokens.authenticate(secret)).thenReturn(Optional.of(new PersonalAccessToken()));
        assertThat(service.poll(secret).get("status")).isEqualTo("approved");
        assertThatThrownBy(() -> service.approve(5L, grant.getUserCode(), "team", true, true, 365)).isInstanceOf(ResponseStatusException.class);
        when(tokens.authenticate(secret)).thenReturn(Optional.empty());
        assertThat(service.poll(secret).get("status")).isEqualTo("revoked");
        grant.setExpiresAt(Instant.now().minusSeconds(1));
        assertThatThrownBy(() -> service.poll(secret)).isInstanceOf(ResponseStatusException.class);
    }

    @Test
    void failedMembershipCheckCannotApproveTheGrant() {
        var grant = new IdeAuthorization();
        grant.setWorkspaceSlug("team"); grant.setDeviceHash("hash"); grant.setExpiresAt(Instant.now().plusSeconds(600));
        when(requests.findForApproval("CODE")).thenReturn(Optional.of(grant));
        var workspace = mock(Workspace.class); when(workspace.getId()).thenReturn(10L);
        when(workspaces.findBySlug("team")).thenReturn(Optional.of(workspace));
        when(tokens.create(5L, 10L, "IDE connection", true, 90, "hash")).thenThrow(new SecurityException());
        assertThatThrownBy(() -> service.approve(5L, "CODE", "team", true, true, 90)).isInstanceOf(SecurityException.class);
        assertThat(grant.isApproved()).isFalse();
        verify(requests, never()).save(any());
    }
}
