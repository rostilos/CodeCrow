package org.rostilos.codecrow.events.notification;

import org.rostilos.codecrow.events.CodecrowEvent;

/**
 * Event fired when workspace quota is approaching or exceeded.
 */
public class QuotaWarningEvent extends CodecrowEvent {

    public enum QuotaType {
        ANALYSES_PER_MONTH,
        STORAGE,
        PROJECTS,
        MEMBERS
    }

    public enum WarningLevel {
        /**
         * 80% of quota used
         */
        WARNING,
        
        /**
         * 100% of quota reached
         */
        EXCEEDED
    }

    private final Long workspaceId;
    private final String workspaceName;
    private final QuotaType quotaType;
    private final WarningLevel warningLevel;
    private final long currentUsage;
    private final long quotaLimit;
    private final int usagePercentage;

    public QuotaWarningEvent(Object source, Long workspaceId, String workspaceName,
                             QuotaType quotaType, WarningLevel warningLevel,
                             long currentUsage, long quotaLimit) {
        super(source);
        this.workspaceId = workspaceId;
        this.workspaceName = workspaceName;
        this.quotaType = quotaType;
        this.warningLevel = warningLevel;
        this.currentUsage = currentUsage;
        this.quotaLimit = quotaLimit;
        this.usagePercentage = quotaLimit > 0 ? (int) ((currentUsage * 100) / quotaLimit) : 0;
    }

    @Override
    public String getEventType() {
        return "QUOTA_WARNING";
    }

    public Long getWorkspaceId() {
        return workspaceId;
    }

    public String getWorkspaceName() {
        return workspaceName;
    }

    public QuotaType getQuotaType() {
        return quotaType;
    }

    public WarningLevel getWarningLevel() {
        return warningLevel;
    }

    public long getCurrentUsage() {
        return currentUsage;
    }

    public long getQuotaLimit() {
        return quotaLimit;
    }

    public int getUsagePercentage() {
        return usagePercentage;
    }

    public boolean isExceeded() {
        return warningLevel == WarningLevel.EXCEEDED;
    }
}
