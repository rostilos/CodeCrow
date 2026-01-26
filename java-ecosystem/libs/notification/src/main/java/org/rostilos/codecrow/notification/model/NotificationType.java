package org.rostilos.codecrow.notification.model;

/**
 * Enum representing all notification types in the system.
 * Organized by scope: WORKSPACE-based and APP-based notifications.
 */
public enum NotificationType {
    
    // ===== WORKSPACE-BASED NOTIFICATIONS =====
    
    /**
     * VCS connection token is approaching expiration.
     * Sent to workspace admins/owners when OAuth tokens need refresh.
     */
    TOKEN_EXPIRING("Token Expiring", NotificationScope.WORKSPACE, NotificationPriority.HIGH),
    
    /**
     * VCS connection token has expired.
     * Critical notification requiring immediate action.
     */
    TOKEN_EXPIRED("Token Expired", NotificationScope.WORKSPACE, NotificationPriority.CRITICAL),
    
    /**
     * Workspace ownership is being transferred to another user.
     * Sent to both old and new owners.
     */
    WORKSPACE_OWNERSHIP_TRANSFER("Ownership Transfer", NotificationScope.WORKSPACE, NotificationPriority.HIGH),
    
    /**
     * User has been invited to join a workspace.
     */
    WORKSPACE_INVITATION("Workspace Invitation", NotificationScope.WORKSPACE, NotificationPriority.MEDIUM),
    
    /**
     * User's role in workspace has changed.
     */
    WORKSPACE_ROLE_CHANGED("Role Changed", NotificationScope.WORKSPACE, NotificationPriority.MEDIUM),
    
    /**
     * User has been removed from a workspace.
     */
    WORKSPACE_MEMBER_REMOVED("Member Removed", NotificationScope.WORKSPACE, NotificationPriority.HIGH),
    
    /**
     * Billing-related notification for workspace.
     * Includes payment reminders, plan changes, usage alerts.
     */
    BILLING_ALERT("Billing Alert", NotificationScope.WORKSPACE, NotificationPriority.HIGH),
    
    /**
     * Workspace usage quota warning (approaching limits).
     */
    QUOTA_WARNING("Quota Warning", NotificationScope.WORKSPACE, NotificationPriority.MEDIUM),
    
    /**
     * Workspace usage quota exceeded.
     */
    QUOTA_EXCEEDED("Quota Exceeded", NotificationScope.WORKSPACE, NotificationPriority.CRITICAL),
    
    /**
     * Analysis completed notification.
     */
    ANALYSIS_COMPLETED("Analysis Completed", NotificationScope.WORKSPACE, NotificationPriority.LOW),
    
    /**
     * Analysis failed notification.
     */
    ANALYSIS_FAILED("Analysis Failed", NotificationScope.WORKSPACE, NotificationPriority.HIGH),
    
    // ===== APP-BASED NOTIFICATIONS =====
    
    /**
     * System-wide announcements from CodeCrow team.
     * Maintenance windows, important updates, etc.
     */
    SYSTEM_ANNOUNCEMENT("System Announcement", NotificationScope.APP, NotificationPriority.MEDIUM),
    
    /**
     * New feature or version update notification.
     */
    PRODUCT_UPDATE("Product Update", NotificationScope.APP, NotificationPriority.LOW),
    
    /**
     * Security-related notification (e.g., login from new device).
     */
    SECURITY_ALERT("Security Alert", NotificationScope.APP, NotificationPriority.CRITICAL),
    
    /**
     * Account-related notification (email change, password change, etc.).
     */
    ACCOUNT_UPDATE("Account Update", NotificationScope.APP, NotificationPriority.HIGH);
    
    private final String displayName;
    private final NotificationScope scope;
    private final NotificationPriority defaultPriority;
    
    NotificationType(String displayName, NotificationScope scope, NotificationPriority defaultPriority) {
        this.displayName = displayName;
        this.scope = scope;
        this.defaultPriority = defaultPriority;
    }
    
    public String getDisplayName() {
        return displayName;
    }
    
    public NotificationScope getScope() {
        return scope;
    }
    
    public NotificationPriority getDefaultPriority() {
        return defaultPriority;
    }
    
    public boolean isWorkspaceScoped() {
        return scope == NotificationScope.WORKSPACE;
    }
    
    public boolean isAppScoped() {
        return scope == NotificationScope.APP;
    }
}
