package org.rostilos.codecrow.core.model.codeanalysis;

/**
 * Result of code analysis based on quality gate evaluation.
 * 
 * PASSED - Analysis completed and all quality gate conditions were met
 * FAILED - Analysis completed but some quality gate conditions were not met
 * SKIPPED - Analysis was skipped (e.g., no quality gate configured)
 */
public enum AnalysisResult {
    PASSED,
    FAILED,
    SKIPPED
}
