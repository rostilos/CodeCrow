package org.rostilos.codecrow.webserver.workspace.dto.response;

import org.rostilos.codecrow.core.model.workspace.WorkspaceOwnershipTransfer;
import org.rostilos.codecrow.core.model.user.User;

import java.time.Instant;
import java.util.UUID;

public record OwnershipTransferDTO(
        UUID id,
        Long workspaceId,
        String workspaceSlug,
        String workspaceName,
        Long fromUserId,
        String fromUsername,
        Long toUserId,
        String toUsername,
        String status,
        Instant initiatedAt,
        Instant expiresAt,
        Instant completedAt,
        boolean canBeCancelled,
        boolean canBeCompleted,
        boolean isExpired
) {
    public static OwnershipTransferDTO fromEntity(WorkspaceOwnershipTransfer transfer, User fromUser, User toUser) {
        return new OwnershipTransferDTO(
                transfer.getId(),
                transfer.getWorkspace().getId(),
                transfer.getWorkspace().getSlug(),
                transfer.getWorkspace().getName(),
                transfer.getFromUserId(),
                fromUser != null ? fromUser.getUsername() : null,
                transfer.getToUserId(),
                toUser != null ? toUser.getUsername() : null,
                transfer.getStatus().name(),
                transfer.getInitiatedAt(),
                transfer.getExpiresAt(),
                transfer.getCompletedAt(),
                transfer.canBeCancelled(),
                transfer.canBeCompleted(),
                transfer.isExpired()
        );
    }
}
