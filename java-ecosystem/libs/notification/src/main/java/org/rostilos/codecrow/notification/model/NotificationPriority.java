package org.rostilos.codecrow.notification.model;

/**
 * Priority level for notifications.
 * Affects display order and delivery channels.
 */
public enum NotificationPriority {
    /**
     * Low priority - informational only.
     * In-app only by default.
     */
    LOW(1),
    
    /**
     * Medium priority - notable but not urgent.
     * In-app, optional email.
     */
    MEDIUM(2),
    
    /**
     * High priority - requires attention.
     * In-app + email by default.
     */
    HIGH(3),
    
    /**
     * Critical priority - immediate action required.
     * All channels, cannot be muted.
     */
    CRITICAL(4);
    
    private final int level;
    
    NotificationPriority(int level) {
        this.level = level;
    }
    
    public int getLevel() {
        return level;
    }
    
    public boolean isHigherThan(NotificationPriority other) {
        return this.level > other.level;
    }
}
