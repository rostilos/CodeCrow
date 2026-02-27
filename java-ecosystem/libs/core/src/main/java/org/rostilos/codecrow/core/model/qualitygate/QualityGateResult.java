package org.rostilos.codecrow.core.model.qualitygate;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;

import java.util.List;

/**
 * Result of quality gate evaluation against a code analysis.
 * Used by all VCS platform reporters to determine pass/fail status.
 */
public record QualityGateResult(
        AnalysisResult result,
        String qualityGateName,
        List<ConditionResult> conditionResults
) {
    /**
     * Creates a result indicating no quality gate was configured.
     */
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

    /**
     * Returns only the conditions that failed evaluation.
     */
    public List<ConditionResult> getFailedConditions() {
        return conditionResults.stream()
                .filter(c -> !c.passed())
                .toList();
    }

    /**
     * Returns a human-readable summary of the evaluation result.
     */
    public String getSummary() {
        if (isSkipped()) {
            return "Quality Gate: Skipped (not configured)";
        }
        if (isPassed()) {
            return String.format("Quality Gate '%s': PASSED", qualityGateName);
        }
        List<ConditionResult> failed = getFailedConditions();
        return String.format("Quality Gate '%s': FAILED (%d condition%s failed)",
                qualityGateName, failed.size(), failed.size() == 1 ? "" : "s");
    }
}
