package org.rostilos.codecrow.core.dto.workspace;

import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.OffsetDateTime;

public record WorkspaceDTO(
        Long id,
        String slug,
        String name,
        String description,
        boolean active,
        Long membersCount,
        OffsetDateTime updatedAt,
        OffsetDateTime createdAt
) {
    public static WorkspaceDTO fromWorkspace(Workspace workspace) {
        return fromWorkspace(workspace, 0L);
    }

    public static WorkspaceDTO fromWorkspace(Workspace workspace, Long membersCount) {
        return new WorkspaceDTO(
                workspace.getId(),
                workspace.getSlug(),
                workspace.getName(),
                workspace.getDescription(),
                workspace.getIsActive(),
                membersCount,
                workspace.getUpdatedAt(),
                workspace.getCreatedAt()
        );
    }
}
