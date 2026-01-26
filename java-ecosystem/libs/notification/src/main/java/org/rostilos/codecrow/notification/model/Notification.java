package org.rostilos.codecrow.notification.model;

import jakarta.persistence.*;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.Map;

/**
 * Entity representing a notification sent to a user.
 */
@Entity
@Table(name = "notification", indexes = {
        @Index(name = "idx_notification_user_read", columnList = "user_id, is_read"),
        @Index(name = "idx_notification_user_created", columnList = "user_id, created_at"),
        @Index(name = "idx_notification_workspace", columnList = "workspace_id"),
        @Index(name = "idx_notification_type", columnList = "type")
})
public class Notification {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workspace_id")
    private Workspace workspace;

    @Enumerated(EnumType.STRING)
    @Column(name = "type", nullable = false, length = 50)
    private NotificationType type;

    @Enumerated(EnumType.STRING)
    @Column(name = "priority", nullable = false, length = 20)
    private NotificationPriority priority;

    @Column(name = "title", nullable = false, length = 255)
    private String title;

    @Column(name = "message", nullable = false, columnDefinition = "TEXT")
    private String message;

    @Column(name = "is_read", nullable = false)
    private boolean read = false;

    @Column(name = "is_email_sent", nullable = false)
    private boolean emailSent = false;

    @Column(name = "created_at", nullable = false, updatable = false)
    private OffsetDateTime createdAt = OffsetDateTime.now();

    @Column(name = "read_at")
    private OffsetDateTime readAt;

    @Column(name = "expires_at")
    private OffsetDateTime expiresAt;

    /**
     * Optional link to navigate when notification is clicked.
     * Can be relative path (e.g., "/workspace/my-ws/settings") or external URL.
     */
    @Column(name = "action_url", length = 500)
    private String actionUrl;

    /**
     * Label for the action button (e.g., "View Details", "Reconnect Now").
     */
    @Column(name = "action_label", length = 100)
    private String actionLabel;

    /**
     * Additional metadata stored as JSON.
     * Useful for storing context-specific data (e.g., project name, analysis ID).
     */
    @Column(name = "metadata", columnDefinition = "TEXT")
    private String metadataJson;

    @Transient
    private Map<String, Object> metadata;

    public Notification() {
    }

    public Notification(User user, NotificationType type, String title, String message) {
        this.user = user;
        this.type = type;
        this.priority = type.getDefaultPriority();
        this.title = title;
        this.message = message;
    }

    public Notification(User user, Workspace workspace, NotificationType type, String title, String message) {
        this(user, type, title, message);
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

    public NotificationPriority getPriority() {
        return priority;
    }

    public void setPriority(NotificationPriority priority) {
        this.priority = priority;
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }

    public boolean isRead() {
        return read;
    }

    public void setRead(boolean read) {
        this.read = read;
        if (read && this.readAt == null) {
            this.readAt = OffsetDateTime.now();
        }
    }

    public boolean isEmailSent() {
        return emailSent;
    }

    public void setEmailSent(boolean emailSent) {
        this.emailSent = emailSent;
    }

    public OffsetDateTime getCreatedAt() {
        return createdAt;
    }

    public OffsetDateTime getReadAt() {
        return readAt;
    }

    public void setReadAt(OffsetDateTime readAt) {
        this.readAt = readAt;
    }

    public OffsetDateTime getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(OffsetDateTime expiresAt) {
        this.expiresAt = expiresAt;
    }

    public String getActionUrl() {
        return actionUrl;
    }

    public void setActionUrl(String actionUrl) {
        this.actionUrl = actionUrl;
    }

    public String getActionLabel() {
        return actionLabel;
    }

    public void setActionLabel(String actionLabel) {
        this.actionLabel = actionLabel;
    }

    public String getMetadataJson() {
        return metadataJson;
    }

    public void setMetadataJson(String metadataJson) {
        this.metadataJson = metadataJson;
        this.metadata = null; // Clear cached metadata
    }

    public boolean isExpired() {
        return expiresAt != null && OffsetDateTime.now().isAfter(expiresAt);
    }

    /**
     * Mark notification as read.
     */
    public void markAsRead() {
        if (!this.read) {
            this.read = true;
            this.readAt = OffsetDateTime.now();
        }
    }
}
