package org.rostilos.codecrow.notification.service;

import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.notification.model.Notification;
import org.rostilos.codecrow.notification.model.NotificationPreference;
import org.rostilos.codecrow.notification.model.NotificationPriority;
import org.rostilos.codecrow.notification.model.NotificationType;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;

import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Service interface for managing user notifications.
 */
public interface NotificationService {

    // ===== NOTIFICATION CREATION =====

    /**
     * Create and send a notification to a user (app-scoped).
     *
     * @param user    Target user
     * @param type    Notification type
     * @param title   Short title
     * @param message Detailed message
     * @return Created notification
     */
    Notification createNotification(User user, NotificationType type, String title, String message);

    /**
     * Create and send a notification to a user in a workspace context.
     *
     * @param user      Target user
     * @param workspace Workspace context
     * @param type      Notification type
     * @param title     Short title
     * @param message   Detailed message
     * @return Created notification
     */
    Notification createNotification(User user, Workspace workspace, NotificationType type, 
                                    String title, String message);

    /**
     * Create a notification with full options.
     */
    Notification createNotification(NotificationBuilder builder);

    /**
     * Send notification to all members of a workspace with specified roles.
     *
     * @param workspace Target workspace
     * @param type      Notification type
     * @param title     Short title
     * @param message   Detailed message
     * @param roles     Roles to notify (e.g., "OWNER", "ADMIN")
     * @return List of created notifications
     */
    List<Notification> notifyWorkspaceMembers(Workspace workspace, NotificationType type,
                                               String title, String message, String... roles);

    /**
     * Send notification to all workspace admins and owners.
     */
    List<Notification> notifyWorkspaceAdmins(Workspace workspace, NotificationType type,
                                              String title, String message);

    // ===== NOTIFICATION RETRIEVAL =====

    /**
     * Get notifications for a user.
     */
    Page<Notification> getUserNotifications(Long userId, Pageable pageable);

    /**
     * Get unread notifications for a user.
     */
    Page<Notification> getUnreadNotifications(Long userId, Pageable pageable);

    /**
     * Get notifications for a user in a workspace.
     */
    Page<Notification> getWorkspaceNotifications(Long userId, Long workspaceId, Pageable pageable);

    /**
     * Get unread notification count for a user.
     */
    long getUnreadCount(Long userId);

    /**
     * Get unread count for a user in a workspace.
     */
    long getUnreadCount(Long userId, Long workspaceId);

    /**
     * Get a notification by ID (with ownership check).
     */
    Optional<Notification> getNotification(Long notificationId, Long userId);

    // ===== NOTIFICATION MANAGEMENT =====

    /**
     * Mark a single notification as read.
     */
    void markAsRead(Long notificationId, Long userId);

    /**
     * Mark multiple notifications as read.
     */
    void markAsRead(List<Long> notificationIds, Long userId);

    /**
     * Mark all user notifications as read.
     */
    void markAllAsRead(Long userId);

    /**
     * Mark all notifications in a workspace as read.
     */
    void markAllAsRead(Long userId, Long workspaceId);

    /**
     * Delete a notification.
     */
    void deleteNotification(Long notificationId, Long userId);

    // ===== PREFERENCE MANAGEMENT =====

    /**
     * Get all preferences for a user.
     */
    List<NotificationPreference> getUserPreferences(Long userId);

    /**
     * Get preferences for a user in a workspace.
     */
    List<NotificationPreference> getWorkspacePreferences(Long userId, Long workspaceId);

    /**
     * Update a preference.
     */
    NotificationPreference updatePreference(Long userId, NotificationType type, Long workspaceId,
                                            boolean inAppEnabled, boolean emailEnabled,
                                            NotificationPriority minPriority);

    /**
     * Get effective preference for a notification type.
     * Returns workspace-specific if exists, otherwise global, otherwise default.
     */
    NotificationPreference getEffectivePreference(Long userId, NotificationType type, Long workspaceId);

    /**
     * Reset preferences to defaults for a user.
     */
    void resetPreferences(Long userId);

    // ===== BUILDER =====

    /**
     * Builder for creating notifications with all options.
     */
    interface NotificationBuilder {
        NotificationBuilder user(User user);
        NotificationBuilder workspace(Workspace workspace);
        NotificationBuilder type(NotificationType type);
        NotificationBuilder priority(NotificationPriority priority);
        NotificationBuilder title(String title);
        NotificationBuilder message(String message);
        NotificationBuilder actionUrl(String url);
        NotificationBuilder actionLabel(String label);
        NotificationBuilder metadata(Map<String, Object> metadata);
        NotificationBuilder addMetadata(String key, Object value);
        NotificationBuilder expiresInDays(int days);
        NotificationBuilder skipDuplicateCheck();
        Notification send();
    }

    /**
     * Create a new notification builder.
     */
    NotificationBuilder builder();
}
