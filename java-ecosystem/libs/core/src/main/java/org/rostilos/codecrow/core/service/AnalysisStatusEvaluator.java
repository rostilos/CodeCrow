package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateResult;

/**
 * Interface for evaluating the status (PASSED/FAILED/SKIPPED) of a code analysis.
 * <p>
 * This abstraction decouples VCS reporting services from the concrete evaluation mechanism
 * (quality gates). All VCS platform reporters (Bitbucket, GitHub, GitLab, and any future
 * platforms like Azure DevOps) use this interface to determine pass/fail status uniformly.
 * <p>
 * The default implementation ({@link org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator})
 * evaluates the project's quality gate conditions. If no quality gate is configured on the project,
 * the workspace default is used. If neither exists, the evaluation is skipped.
 */
public interface AnalysisStatusEvaluator {

    /**
     * Evaluate the analysis status for a given project.
     * <p>
     * Resolution order for quality gate:
     * <ol>
     *   <li>Project's explicitly assigned quality gate</li>
     *   <li>Workspace's default quality gate</li>
     *   <li>SKIPPED if neither is available</li>
     * </ol>
     *
     * @param analysis The code analysis to evaluate
     * @param project  The project that owns the analysis
     * @return Result containing PASSED/FAILED/SKIPPED with condition details
     */
    QualityGateResult evaluateStatus(CodeAnalysis analysis, Project project);
}
