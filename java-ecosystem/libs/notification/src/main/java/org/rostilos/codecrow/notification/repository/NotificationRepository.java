package org.rostilos.codecrow.notification.repository;

import org.rostilos.codecrow.notification.model.Notification;
import org.rostilos.codecrow.notification.model.NotificationType;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.OffsetDateTime;
import java.util.List;

@Repository
public interface NotificationRepository extends JpaRepository<Notification, Long> {

    /**
     * Find all notifications for a user, ordered by creation time (newest first).
     */
    Page<Notification> findByUserIdOrderByCreatedAtDesc(Long userId, Pageable pageable);

    /**
     * Find unread notifications for a user.
     */
    Page<Notification> findByUserIdAndReadFalseOrderByCreatedAtDesc(Long userId, Pageable pageable);

    /**
     * Find notifications for a user in a specific workspace.
     */
    Page<Notification> findByUserIdAndWorkspaceIdOrderByCreatedAtDesc(Long userId, Long workspaceId, Pageable pageable);

    /**
     * Count unread notifications for a user.
     */
    long countByUserIdAndReadFalse(Long userId);

    /**
     * Count unread notifications for a user in a workspace.
     */
    long countByUserIdAndWorkspaceIdAndReadFalse(Long userId, Long workspaceId);

    /**
     * Mark all notifications as read for a user.
     */
    @Modifying
    @Query("UPDATE Notification n SET n.read = true, n.readAt = :readAt WHERE n.user.id = :userId AND n.read = false")
    int markAllAsReadForUser(@Param("userId") Long userId, @Param("readAt") OffsetDateTime readAt);

    /**
     * Mark all notifications as read for a user in a workspace.
     */
    @Modifying
    @Query("UPDATE Notification n SET n.read = true, n.readAt = :readAt WHERE n.user.id = :userId AND n.workspace.id = :workspaceId AND n.read = false")
    int markAllAsReadForUserInWorkspace(@Param("userId") Long userId, @Param("workspaceId") Long workspaceId, @Param("readAt") OffsetDateTime readAt);

    /**
     * Find notifications of a specific type for a user (for deduplication).
     */
    List<Notification> findByUserIdAndTypeAndCreatedAtAfter(Long userId, NotificationType type, OffsetDateTime after);

    /**
     * Find notifications that need email sending.
     */
    @Query("SELECT n FROM Notification n WHERE n.emailSent = false AND n.createdAt > :after ORDER BY n.createdAt ASC")
    List<Notification> findPendingEmailNotifications(@Param("after") OffsetDateTime after);

    /**
     * Delete expired notifications.
     */
    @Modifying
    @Query("DELETE FROM Notification n WHERE n.expiresAt IS NOT NULL AND n.expiresAt < :now")
    int deleteExpiredNotifications(@Param("now") OffsetDateTime now);

    /**
     * Delete old read notifications (cleanup).
     */
    @Modifying
    @Query("DELETE FROM Notification n WHERE n.read = true AND n.createdAt < :before")
    int deleteOldReadNotifications(@Param("before") OffsetDateTime before);

    /**
     * Find by user and type in workspace (for preventing duplicates).
     */
    @Query("SELECT n FROM Notification n WHERE n.user.id = :userId AND n.type = :type " +
           "AND n.workspace.id = :workspaceId AND n.createdAt > :after AND n.read = false")
    List<Notification> findRecentUnreadByUserTypeAndWorkspace(
            @Param("userId") Long userId,
            @Param("type") NotificationType type,
            @Param("workspaceId") Long workspaceId,
            @Param("after") OffsetDateTime after);
}
