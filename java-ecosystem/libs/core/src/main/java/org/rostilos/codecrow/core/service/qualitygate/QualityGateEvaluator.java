package org.rostilos.codecrow.core.service.qualitygate;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateCondition;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

/**
 * Evaluates code analysis results against a Quality Gate's conditions.
 */
public class QualityGateEvaluator {
    
    private static final Logger log = LoggerFactory.getLogger(QualityGateEvaluator.class);

    /**
     * Evaluate a code analysis against a quality gate.
     * 
     * @param analysis The code analysis to evaluate
     * @param qualityGate The quality gate to evaluate against (can be null)
     * @return QualityGateResult containing pass/fail status and details
     */
    public QualityGateResult evaluate(CodeAnalysis analysis, QualityGate qualityGate) {
        if (qualityGate == null || !qualityGate.isActive()) {
            log.debug("No active quality gate configured, skipping evaluation");
            return QualityGateResult.skipped();
        }

        List<ConditionResult> conditionResults = new ArrayList<>();
        boolean overallPass = true;

        for (QualityGateCondition condition : qualityGate.getConditions()) {
            if (!condition.isEnabled()) {
                continue;
            }

            int actualValue = getMetricValue(analysis, condition);
            boolean conditionFails = condition.fails(actualValue);
            
            if (conditionFails) {
                overallPass = false;
            }

            conditionResults.add(new ConditionResult(
                    condition.getMetric(),
                    condition.getSeverity(),
                    condition.getComparator().getSymbol(),
                    condition.getThresholdValue(),
                    actualValue,
                    !conditionFails // passes = !fails
            ));
        }

        AnalysisResult result = overallPass ? AnalysisResult.PASSED : AnalysisResult.FAILED;
        log.info("Quality Gate '{}' evaluation: {} ({} conditions evaluated)", 
                qualityGate.getName(), result, conditionResults.size());

        return new QualityGateResult(result, qualityGate.getName(), conditionResults);
    }

    /**
     * Get the actual value for a metric from the analysis.
     */
    private int getMetricValue(CodeAnalysis analysis, QualityGateCondition condition) {
        return switch (condition.getMetric()) {
            case ISSUES_BY_SEVERITY -> getIssueCountBySeverity(analysis, condition.getSeverity());
            case NEW_ISSUES -> analysis.getTotalIssues() - analysis.getResolvedCount();
            case ISSUES_BY_CATEGORY -> 0; // TODO: Implement category-based counting
        };
    }

    /**
     * Get issue count by severity.
     */
    private int getIssueCountBySeverity(CodeAnalysis analysis, IssueSeverity severity) {
        if (severity == null) {
            return analysis.getTotalIssues();
        }
        return switch (severity) {
            case HIGH -> analysis.getHighSeverityCount();
            case MEDIUM -> analysis.getMediumSeverityCount();
            case LOW -> analysis.getLowSeverityCount();
            case INFO -> analysis.getInfoSeverityCount();
            case RESOLVED -> analysis.getResolvedCount();
        };
    }

    /**
     * Result of evaluating a single condition.
     */
    public record ConditionResult(
            QualityGateMetric metric,
            IssueSeverity severity,
            String comparator,
            int threshold,
            int actualValue,
            boolean passed
    ) {
        public String getDescription() {
            String severityStr = severity != null ? severity.name() + " " : "";
            return String.format("%s%s %s %d (actual: %d) - %s",
                    severityStr,
                    metric.getDisplayName(),
                    comparator,
                    threshold,
                    actualValue,
                    passed ? "PASSED" : "FAILED");
        }
    }

    /**
     * Result of quality gate evaluation.
     */
    public record QualityGateResult(
            AnalysisResult result,
            String qualityGateName,
            List<ConditionResult> conditionResults
    ) {
        public static QualityGateResult skipped() {
            return new QualityGateResult(AnalysisResult.SKIPPED, null, List.of());
        }

        public boolean isPassed() {
            return result == AnalysisResult.PASSED;
        }

        public boolean isFailed() {
            return result == AnalysisResult.FAILED;
        }

        public boolean isSkipped() {
            return result == AnalysisResult.SKIPPED;
        }

        public List<ConditionResult> getFailedConditions() {
            return conditionResults.stream()
                    .filter(c -> !c.passed())
                    .toList();
        }
    }
}
