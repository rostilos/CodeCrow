package org.rostilos.codecrow.notification.repository;

import org.rostilos.codecrow.notification.model.NotificationPreference;
import org.rostilos.codecrow.notification.model.NotificationType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface NotificationPreferenceRepository extends JpaRepository<NotificationPreference, Long> {

    /**
     * Find all global preferences for a user (not workspace-specific).
     */
    @Query("SELECT p FROM NotificationPreference p WHERE p.user.id = :userId AND p.workspace IS NULL")
    List<NotificationPreference> findGlobalPreferencesByUserId(@Param("userId") Long userId);

    /**
     * Find all preferences for a user in a specific workspace.
     */
    List<NotificationPreference> findByUserIdAndWorkspaceId(Long userId, Long workspaceId);

    /**
     * Find a specific preference for user and type (global).
     */
    @Query("SELECT p FROM NotificationPreference p WHERE p.user.id = :userId AND p.type = :type AND p.workspace IS NULL")
    Optional<NotificationPreference> findGlobalPreference(@Param("userId") Long userId, @Param("type") NotificationType type);

    /**
     * Find a specific preference for user, type, and workspace.
     */
    Optional<NotificationPreference> findByUserIdAndTypeAndWorkspaceId(Long userId, NotificationType type, Long workspaceId);

    /**
     * Find the effective preference for a notification.
     * First tries workspace-specific, then falls back to global.
     */
    @Query("SELECT p FROM NotificationPreference p WHERE p.user.id = :userId AND p.type = :type " +
           "AND (p.workspace.id = :workspaceId OR p.workspace IS NULL) " +
           "ORDER BY CASE WHEN p.workspace IS NOT NULL THEN 0 ELSE 1 END")
    List<NotificationPreference> findEffectivePreference(
            @Param("userId") Long userId,
            @Param("type") NotificationType type,
            @Param("workspaceId") Long workspaceId);

    /**
     * Delete all preferences for a user in a workspace.
     */
    void deleteByUserIdAndWorkspaceId(Long userId, Long workspaceId);

    /**
     * Check if user has any custom preferences.
     */
    boolean existsByUserId(Long userId);
}
