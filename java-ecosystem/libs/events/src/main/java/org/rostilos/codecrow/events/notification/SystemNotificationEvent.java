package org.rostilos.codecrow.events.notification;

import org.rostilos.codecrow.events.CodecrowEvent;

import java.util.Set;

/**
 * Event fired for system-wide announcements and product updates.
 */
public class SystemNotificationEvent extends CodecrowEvent {

    public enum SystemEventType {
        /**
         * System-wide announcement (maintenance, outage, etc.)
         */
        ANNOUNCEMENT,
        
        /**
         * New feature or product update
         */
        PRODUCT_UPDATE,
        
        /**
         * Security-related announcement
         */
        SECURITY_ADVISORY
    }

    private final SystemEventType systemEventType;
    private final String title;
    private final String message;
    private final String actionUrl;
    private final String actionLabel;
    
    /**
     * If null, notification is sent to all users.
     * Otherwise, only to specified user IDs.
     */
    private final Set<Long> targetUserIds;
    
    /**
     * If true, notification is sent to all users.
     */
    private final boolean broadcastToAll;

    public SystemNotificationEvent(Object source, SystemEventType systemEventType,
                                    String title, String message,
                                    String actionUrl, String actionLabel,
                                    Set<Long> targetUserIds, boolean broadcastToAll) {
        super(source);
        this.systemEventType = systemEventType;
        this.title = title;
        this.message = message;
        this.actionUrl = actionUrl;
        this.actionLabel = actionLabel;
        this.targetUserIds = targetUserIds;
        this.broadcastToAll = broadcastToAll;
    }

    /**
     * Create a broadcast announcement to all users.
     */
    public static SystemNotificationEvent broadcast(Object source, SystemEventType type,
                                                     String title, String message) {
        return new SystemNotificationEvent(source, type, title, message, null, null, null, true);
    }

    /**
     * Create a targeted announcement to specific users.
     */
    public static SystemNotificationEvent targeted(Object source, SystemEventType type,
                                                    String title, String message, Set<Long> userIds) {
        return new SystemNotificationEvent(source, type, title, message, null, null, userIds, false);
    }

    @Override
    public String getEventType() {
        return "SYSTEM_NOTIFICATION";
    }

    public SystemEventType getSystemEventType() {
        return systemEventType;
    }

    public String getTitle() {
        return title;
    }

    public String getMessage() {
        return message;
    }

    public String getActionUrl() {
        return actionUrl;
    }

    public String getActionLabel() {
        return actionLabel;
    }

    public Set<Long> getTargetUserIds() {
        return targetUserIds;
    }

    public boolean isBroadcastToAll() {
        return broadcastToAll;
    }
}
