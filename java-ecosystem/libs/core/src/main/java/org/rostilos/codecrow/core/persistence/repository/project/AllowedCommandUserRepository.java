package org.rostilos.codecrow.core.persistence.repository.project;

import org.rostilos.codecrow.core.model.project.AllowedCommandUser;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * Repository for managing allowed command users.
 */
@Repository
public interface AllowedCommandUserRepository extends JpaRepository<AllowedCommandUser, UUID> {
    
    /**
     * Find all allowed users for a project.
     */
    List<AllowedCommandUser> findByProjectId(Long projectId);
    
    /**
     * Find all enabled allowed users for a project.
     */
    List<AllowedCommandUser> findByProjectIdAndEnabledTrue(Long projectId);
    
    /**
     * Find a specific user by project and VCS user ID.
     */
    Optional<AllowedCommandUser> findByProjectIdAndVcsUserId(Long projectId, String vcsUserId);
    
    /**
     * Find a specific user by project and VCS username.
     */
    Optional<AllowedCommandUser> findByProjectIdAndVcsUsername(Long projectId, String vcsUsername);
    
    /**
     * Check if a user is allowed for a project.
     */
    boolean existsByProjectIdAndVcsUserIdAndEnabledTrue(Long projectId, String vcsUserId);
    
    /**
     * Check if a user (by username) is allowed for a project.
     */
    boolean existsByProjectIdAndVcsUsernameAndEnabledTrue(Long projectId, String vcsUsername);
    
    /**
     * Delete all users for a project.
     */
    @Modifying
    @Query("DELETE FROM AllowedCommandUser u WHERE u.project.id = :projectId")
    void deleteByProjectId(@Param("projectId") Long projectId);
    
    /**
     * Delete synced users for a project (to refresh from VCS).
     */
    @Modifying
    @Query("DELETE FROM AllowedCommandUser u WHERE u.project.id = :projectId AND u.syncedFromVcs = true")
    void deleteSyncedByProjectId(@Param("projectId") Long projectId);
    
    /**
     * Mark all synced users as disabled (for refresh).
     */
    @Modifying
    @Query("UPDATE AllowedCommandUser u SET u.enabled = false WHERE u.project.id = :projectId AND u.syncedFromVcs = true")
    void disableSyncedByProjectId(@Param("projectId") Long projectId);
    
    /**
     * Update last synced timestamp for synced users.
     */
    @Modifying
    @Query("UPDATE AllowedCommandUser u SET u.lastSyncedAt = :timestamp WHERE u.project.id = :projectId AND u.syncedFromVcs = true")
    void updateLastSyncedAt(@Param("projectId") Long projectId, @Param("timestamp") OffsetDateTime timestamp);
    
    /**
     * Count allowed users for a project.
     */
    long countByProjectId(Long projectId);
    
    /**
     * Count enabled allowed users for a project.
     */
    long countByProjectIdAndEnabledTrue(Long projectId);
    
    /**
     * Find users by VCS provider for a project.
     */
    List<AllowedCommandUser> findByProjectIdAndVcsProvider(Long projectId, EVcsProvider vcsProvider);
    
    /**
     * Delete a specific user from a project.
     */
    @Modifying
    @Query("DELETE FROM AllowedCommandUser u WHERE u.project.id = :projectId AND u.vcsUserId = :vcsUserId")
    void deleteByProjectIdAndVcsUserId(@Param("projectId") Long projectId, @Param("vcsUserId") String vcsUserId);
    
    /**
     * Check if user exists (regardless of enabled status).
     */
    boolean existsByProjectIdAndVcsUserId(Long projectId, String vcsUserId);
}
