package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.*;
import org.rostilos.codecrow.core.model.user.status.EStatus;
import org.rostilos.codecrow.core.model.workspace.*;
import org.rostilos.codecrow.core.persistence.repository.user.PersonalAccessTokenRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceMemberRepository;
import java.time.Instant;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class PersonalAccessTokenServiceTest {
    private final PersonalAccessTokenRepository repository = mock(PersonalAccessTokenRepository.class);
    private final WorkspaceMemberRepository members = mock(WorkspaceMemberRepository.class);
    private final PersonalAccessTokenService service = new PersonalAccessTokenService(repository, members);
    private final Workspace workspace = mock(Workspace.class);
    private final User user = mock(User.class);
    private final WorkspaceMember member = new WorkspaceMember(workspace, user, EWorkspaceRole.MEMBER, EMembershipStatus.ACTIVE);

    private void member() {
        when(workspace.getId()).thenReturn(10L);
        when(workspace.getSlug()).thenReturn("team");
        when(user.getId()).thenReturn(5L);
        when(user.getStatus()).thenReturn(EStatus.STATUS_ACTIVE);
        when(members.findByWorkspaceIdAndUserId(10L, 5L)).thenReturn(Optional.of(member));
        when(repository.save(any())).thenAnswer(i -> i.getArgument(0));
    }

    @Test
    void membersCreateOwnHashedScopedTokensAndRemovalImmediatelyInvalidatesThem() {
        member();
        String secret = PersonalAccessTokenService.secret();
        var token = service.create(5L, 10L, "IDE", false, 90, PersonalAccessTokenService.hash(secret));
        assertThat(token.getTokenHash()).hasSize(64).doesNotContain(secret);
        assertThat(token.getUser()).isSameAs(user);
        assertThat(token.getWorkspace()).isSameAs(workspace);
        assertThat(token.isWriteAllowed()).isFalse();
        when(repository.findByTokenHash(token.getTokenHash())).thenReturn(Optional.of(token));
        assertThat(service.authenticate(secret)).contains(token);
        when(members.findByWorkspaceIdAndUserId(10L, 5L)).thenReturn(Optional.empty());
        assertThat(service.authenticate(secret)).isEmpty();
    }

    @Test
    void expiredRevokedAndDisabledCredentialsCannotAuthenticate() {
        member();
        String secret = PersonalAccessTokenService.secret();
        var token = service.create(5L, 10L, "IDE", true, 30, PersonalAccessTokenService.hash(secret));
        when(repository.findByTokenHash(token.getTokenHash())).thenReturn(Optional.of(token));
        when(user.getStatus()).thenReturn(EStatus.STATUS_DISABLED);
        assertThat(service.authenticate(secret)).isEmpty();
        when(user.getStatus()).thenReturn(EStatus.STATUS_ACTIVE);
        token.setExpiresAt(Instant.now().minusSeconds(1));
        assertThat(service.authenticate(secret)).isEmpty();
        when(repository.findByTokenHash(token.getTokenHash())).thenReturn(Optional.empty());
        assertThat(service.authenticate(secret)).isEmpty();
        assertThat(service.authenticate("ccpat_invalid")).isEmpty();
    }

    @Test
    void scopesNeverCrossWorkspacesAndWritesRequireExplicitScope() {
        member();
        var token = service.create(5L, 10L, "IDE", false, 90, "hash");
        assertThat(PersonalAccessTokenService.permits(token, "GET", "team")).isTrue();
        assertThat(PersonalAccessTokenService.permits(token, "PATCH", "team")).isFalse();
        token.setWriteAllowed(true);
        assertThat(PersonalAccessTokenService.permits(token, "PATCH", "team")).isTrue();
        assertThat(PersonalAccessTokenService.permits(token, "GET", "other")).isFalse();
        assertThat(PersonalAccessTokenService.permits(token, "GET", null)).isFalse();
        assertThatThrownBy(() -> service.create(6L, 10L, "IDE", true, 90, "hash"))
                .isInstanceOf(SecurityException.class);
        assertThatThrownBy(() -> service.create(5L, 10L, "IDE", true, 0, "hash"))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
