package org.rostilos.codecrow.notification.model;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.OffsetDateTime;

/**
 * Entity representing a user's notification preferences.
 * Can be configured per notification type, either globally or per workspace.
 */
@Entity
@Table(name = "notification_preference", 
       uniqueConstraints = {
           @UniqueConstraint(columnNames = {"user_id", "type", "workspace_id"})
       },
       indexes = {
           @Index(name = "idx_notif_pref_user", columnList = "user_id"),
           @Index(name = "idx_notif_pref_user_workspace", columnList = "user_id, workspace_id")
       })
public class NotificationPreference {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    /**
     * If null, this is a global preference for the user.
     * If set, this preference applies only to this workspace.
     */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id")
    private Workspace workspace;

    @Enumerated(EnumType.STRING)
    @Column(name = "type", nullable = false, length = 50)
    private NotificationType type;

    /**
     * Whether to show in-app notifications for this type.
     */
    @Column(name = "in_app_enabled", nullable = false)
    private boolean inAppEnabled = true;

    /**
     * Whether to send email notifications for this type.
     */
    @Column(name = "email_enabled", nullable = false)
    private boolean emailEnabled = true;

    /**
     * Minimum priority level to receive notifications.
     * Notifications below this priority are suppressed.
     */
    @Enumerated(EnumType.STRING)
    @Column(name = "min_priority", nullable = false, length = 20)
    private NotificationPriority minPriority = NotificationPriority.LOW;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    @PreUpdate
    public void onUpdate() {
        this.updatedAt = OffsetDateTime.now();
    }

    public NotificationPreference() {
    }

    public NotificationPreference(User user, NotificationType type) {
        this.user = user;
        this.type = type;
    }

    public NotificationPreference(User user, Workspace workspace, NotificationType type) {
        this(user, type);
        this.workspace = workspace;
    }

    // Getters and Setters

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public User getUser() {
        return user;
    }

    public void setUser(User user) {
        this.user = user;
    }

    public Workspace getWorkspace() {
        return workspace;
    }

    public void setWorkspace(Workspace workspace) {
        this.workspace = workspace;
    }

    public NotificationType getType() {
        return type;
    }

    public void setType(NotificationType type) {
        this.type = type;
    }

    public boolean isInAppEnabled() {
        return inAppEnabled;
    }

    public void setInAppEnabled(boolean inAppEnabled) {
        this.inAppEnabled = inAppEnabled;
    }

    public boolean isEmailEnabled() {
        return emailEnabled;
    }

    public void setEmailEnabled(boolean emailEnabled) {
        this.emailEnabled = emailEnabled;
    }

    public NotificationPriority getMinPriority() {
        return minPriority;
    }

    public void setMinPriority(NotificationPriority minPriority) {
        this.minPriority = minPriority;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    /**
     * Check if a notification with given priority should be delivered via this channel.
     */
    public boolean shouldNotify(NotificationPriority priority, boolean isEmail) {
        // Critical notifications always get through
        if (priority == NotificationPriority.CRITICAL) {
            return true;
        }
        
        // Check if channel is enabled
        if (isEmail && !emailEnabled) {
            return false;
        }
        if (!isEmail && !inAppEnabled) {
            return false;
        }
        
        // Check priority threshold
        return priority.getLevel() >= minPriority.getLevel();
    }

    public boolean isGlobal() {
        return workspace == null;
    }
}
