package org.rostilos.codecrow.core.service.qualitygate;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qualitygate.ConditionResult;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateCondition;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateMetric;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateResult;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;
import org.rostilos.codecrow.core.service.AnalysisStatusEvaluator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * Evaluates code analysis results against a Quality Gate's conditions.
 * <p>
 * Implements {@link AnalysisStatusEvaluator} to provide a common abstraction
 * for all VCS platform reporters (Bitbucket, GitHub, GitLab, and future platforms).
 */
@Component
public class QualityGateEvaluator implements AnalysisStatusEvaluator {
    
    private static final Logger log = LoggerFactory.getLogger(QualityGateEvaluator.class);

    private final QualityGateRepository qualityGateRepository;

    public QualityGateEvaluator(QualityGateRepository qualityGateRepository) {
        this.qualityGateRepository = qualityGateRepository;
    }

    /**
     * Evaluate the analysis status for a given project.
     * Resolves the quality gate from the project, falling back to workspace default.
     *
     * @param analysis The code analysis to evaluate
     * @param project  The project that owns the analysis
     * @return Result containing PASSED/FAILED/SKIPPED with condition details
     */
    @Override
    public QualityGateResult evaluateStatus(CodeAnalysis analysis, Project project) {
        QualityGate qualityGate = project.getQualityGate();

        if (qualityGate == null && project.getWorkspace() != null) {
            // Fall back to workspace default quality gate
            qualityGate = qualityGateRepository
                    .findDefaultWithConditions(project.getWorkspace().getId())
                    .orElse(null);

            if (qualityGate != null) {
                log.debug("Using workspace default quality gate '{}' for project '{}'",
                        qualityGate.getName(), project.getNamespace());
            }
        }

        return evaluate(analysis, qualityGate);
    }

    /**
     * Evaluate a code analysis against a specific quality gate.
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
                    condition.getCategory(),
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
            case ISSUES_BY_CATEGORY -> getIssueCountByCategory(analysis, condition.getCategory());
        };
    }

    /**
     * Get issue count by severity (uses unresolved counts from CodeAnalysis).
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
     * Get issue count by category (only unresolved issues).
     */
    private int getIssueCountByCategory(CodeAnalysis analysis, IssueCategory category) {
        if (category == null || analysis.getIssues() == null) {
            return analysis.getTotalIssues();
        }
        return (int) analysis.getIssues().stream()
                .filter(issue -> !issue.isResolved())
                .filter(issue -> category.equals(issue.getIssueCategory()))
                .count();
    }
}
