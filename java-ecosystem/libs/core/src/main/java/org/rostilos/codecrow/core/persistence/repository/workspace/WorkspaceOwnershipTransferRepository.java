package org.rostilos.codecrow.core.persistence.repository.workspace;

import org.rostilos.codecrow.core.model.workspace.WorkspaceOwnershipTransfer;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface WorkspaceOwnershipTransferRepository extends JpaRepository<WorkspaceOwnershipTransfer, UUID> {

    /**
     * Find pending transfer for a workspace
     */
    @Query("SELECT t FROM WorkspaceOwnershipTransfer t WHERE t.workspace.id = :workspaceId AND t.status = 'PENDING'")
    Optional<WorkspaceOwnershipTransfer> findPendingByWorkspaceId(@Param("workspaceId") Long workspaceId);

    /**
     * Find all pending transfers that have expired
     */
    @Query("SELECT t FROM WorkspaceOwnershipTransfer t WHERE t.status = 'PENDING' AND t.expiresAt < :now")
    List<WorkspaceOwnershipTransfer> findExpiredPendingTransfers(@Param("now") Instant now);

    /**
     * Check if there's any pending transfer for a workspace
     */
    @Query("SELECT COUNT(t) > 0 FROM WorkspaceOwnershipTransfer t WHERE t.workspace.id = :workspaceId AND t.status = 'PENDING'")
    boolean existsPendingTransferForWorkspace(@Param("workspaceId") Long workspaceId);

    /**
     * Find all transfers initiated by a user
     */
    @Query("SELECT t FROM WorkspaceOwnershipTransfer t WHERE t.fromUserId = :userId ORDER BY t.initiatedAt DESC")
    List<WorkspaceOwnershipTransfer> findByFromUserId(@Param("userId") Long userId);

    /**
     * Find all transfers targeting a user
     */
    @Query("SELECT t FROM WorkspaceOwnershipTransfer t WHERE t.toUserId = :userId ORDER BY t.initiatedAt DESC")
    List<WorkspaceOwnershipTransfer> findByToUserId(@Param("userId") Long userId);

    /**
     * Find transfer history for a workspace
     */
    @Query("SELECT t FROM WorkspaceOwnershipTransfer t WHERE t.workspace.id = :workspaceId ORDER BY t.initiatedAt DESC")
    List<WorkspaceOwnershipTransfer> findByWorkspaceIdOrderByInitiatedAtDesc(@Param("workspaceId") Long workspaceId);

    /**
     * Mark expired transfers as EXPIRED status (batch update)
     */
    @Modifying
    @Query("UPDATE WorkspaceOwnershipTransfer t SET t.status = 'EXPIRED' WHERE t.status = 'PENDING' AND t.expiresAt < :now")
    int markExpiredTransfers(@Param("now") Instant now);
}
