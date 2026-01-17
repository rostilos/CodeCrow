package org.rostilos.codecrow.core.model.qualitygate;

/**
 * Metrics that can be evaluated in quality gate conditions.
 */
public enum QualityGateMetric {
    ISSUES_BY_SEVERITY("Issues by Severity", "Number of issues filtered by severity level"),
    
    NEW_ISSUES("New Issues", "Total number of new issues found"),
    
    ISSUES_BY_CATEGORY("Issues by Category", "Number of issues filtered by category");

    private final String displayName;
    private final String description;

    QualityGateMetric(String displayName, String description) {
        this.displayName = displayName;
        this.description = description;
    }

    public String getDisplayName() { return displayName; }
    public String getDescription() { return description; }
}
