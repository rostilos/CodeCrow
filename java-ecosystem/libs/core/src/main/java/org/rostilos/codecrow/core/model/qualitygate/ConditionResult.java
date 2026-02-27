package org.rostilos.codecrow.core.model.qualitygate;

import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;

/**
 * Result of evaluating a single quality gate condition.
 */
public record ConditionResult(
        QualityGateMetric metric,
        IssueSeverity severity,
        IssueCategory category,
        String comparator,
        int threshold,
        int actualValue,
        boolean passed
) {
    /**
     * Human-readable description of the condition evaluation.
     */
    public String getDescription() {
        String filterStr = "";
        if (severity != null) {
            filterStr = severity.name() + " ";
        } else if (category != null) {
            filterStr = category.getDisplayName() + " ";
        }
        return String.format("%s%s %s %d (actual: %d) - %s",
                filterStr,
                metric.getDisplayName(),
                comparator,
                threshold,
                actualValue,
                passed ? "PASSED" : "FAILED");
    }
}
