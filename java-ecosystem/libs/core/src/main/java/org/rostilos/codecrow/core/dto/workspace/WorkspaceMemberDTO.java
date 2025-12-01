package org.rostilos.codecrow.core.dto.workspace;

import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;

import java.time.Instant;

public record WorkspaceMemberDTO(
        Long id,
        Long userId,
        String role,
        String status,
        String username,
        String email,
        String avatarUrl,
        Instant joinedAt
)
{
    public static WorkspaceMemberDTO fromEntity(WorkspaceMember workspaceMember) {
        return new WorkspaceMemberDTO(
                workspaceMember.getId(),
                workspaceMember.getUser().getId(),
                workspaceMember.getRole().name(),
                workspaceMember.getStatus().name(),
                workspaceMember.getUser().getUsername(),
                workspaceMember.getUser().getEmail(),
                workspaceMember.getUser().getAvatarUrl(),
                workspaceMember.getJoinedAt()
        );
    }
}
