package org.rostilos.codecrow.security.web;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.workspace.*;
import org.rostilos.codecrow.core.persistence.repository.workspace.*;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import java.util.List;
import java.util.Optional;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class WorkspaceReviewerTest {
    @Test
    void onlyActiveReviewersAdminsAndOwnersMayReview() {
        var members = mock(WorkspaceMemberRepository.class);
        var security = new WorkspaceSecurity(members, mock(WorkspaceRepository.class), mock(ProjectRepository.class));
        var auth = new UsernamePasswordAuthenticationToken(new UserDetailsImpl(5L, "dev", "dev@example.com", "", null, List.of()), null);
        for (var role : EWorkspaceRole.values()) {
            var membership = new WorkspaceMember(null, null, role, EMembershipStatus.ACTIVE);
            when(members.findByWorkspaceIdAndUserId(1L, 5L)).thenReturn(Optional.of(membership));
            assertThat(security.isWorkspaceReviewer(1L, auth)).isEqualTo(role != EWorkspaceRole.MEMBER);
            for (var status : EMembershipStatus.values()) {
                membership.setStatus(status);
                assertThat(security.isWorkspaceReviewer(1L, auth))
                        .isEqualTo(status == EMembershipStatus.ACTIVE && role != EWorkspaceRole.MEMBER);
            }
        }
        assertThat(security.isWorkspaceReviewer(2L, auth)).isFalse();
    }
}
