package org.rostilos.codecrow.core.persistence.repository.vcs;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.PendingForgeInstallation;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

/**
 * Repository for managing pending Forge app installations.
 */
@Repository
public interface PendingForgeInstallationRepository extends JpaRepository<PendingForgeInstallation, Long> {

    /**
     * Find a pending installation by its state token.
     */
    Optional<PendingForgeInstallation> findByState(String state);

    /**
     * Find pending installations for a workspace.
     */
    List<PendingForgeInstallation> findByWorkspaceIdAndStatus(
            Long workspaceId, 
            PendingForgeInstallation.Status status
    );

    /**
     * Find pending installations by user.
     */
    List<PendingForgeInstallation> findByInitiatedByIdAndStatus(
            Long userId, 
            PendingForgeInstallation.Status status
    );

    /**
     * Find pending installations for a user and workspace.
     */
    @Query("SELECT p FROM PendingForgeInstallation p " +
           "WHERE p.workspace.id = :workspaceId " +
           "AND p.initiatedBy.id = :userId " +
           "AND p.status = :status " +
           "AND p.expiresAt > :now " +
           "ORDER BY p.createdAt DESC")
    List<PendingForgeInstallation> findPendingForUserAndWorkspace(
            @Param("workspaceId") Long workspaceId,
            @Param("userId") Long userId,
            @Param("status") PendingForgeInstallation.Status status,
            @Param("now") LocalDateTime now
    );

    /**
     * Find the most recent pending installation for a workspace.
     * Used when matching Forge callback to initiating user.
     */
    @Query("SELECT p FROM PendingForgeInstallation p " +
           "WHERE p.workspace.id = :workspaceId " +
           "AND p.providerType = :providerType " +
           "AND p.status = :status " +
           "AND p.expiresAt > :now " +
           "ORDER BY p.createdAt DESC")
    List<PendingForgeInstallation> findRecentPendingForWorkspace(
            @Param("workspaceId") Long workspaceId,
            @Param("providerType") EVcsProvider providerType,
            @Param("status") PendingForgeInstallation.Status status,
            @Param("now") LocalDateTime now
    );

    /**
     * Find all pending installations that haven't expired yet.
     * Used to match Forge callbacks when we don't have a direct state match.
     */
    @Query("SELECT p FROM PendingForgeInstallation p " +
           "WHERE p.providerType = :providerType " +
           "AND p.status = :status " +
           "AND p.expiresAt > :now " +
           "ORDER BY p.createdAt DESC")
    List<PendingForgeInstallation> findAllPendingNotExpired(
            @Param("providerType") EVcsProvider providerType,
            @Param("status") PendingForgeInstallation.Status status,
            @Param("now") LocalDateTime now
    );

    /**
     * Mark expired installations.
     */
    @Modifying
    @Query("UPDATE PendingForgeInstallation p " +
           "SET p.status = 'EXPIRED', p.completedAt = :now " +
           "WHERE p.status = 'PENDING' AND p.expiresAt < :now")
    int markExpiredInstallations(@Param("now") LocalDateTime now);

    /**
     * Delete old completed/expired/failed installations (cleanup).
     */
    @Modifying
    @Query("DELETE FROM PendingForgeInstallation p " +
           "WHERE p.status != 'PENDING' " +
           "AND p.completedAt < :before")
    int deleteOldCompletedInstallations(@Param("before") LocalDateTime before);
}
