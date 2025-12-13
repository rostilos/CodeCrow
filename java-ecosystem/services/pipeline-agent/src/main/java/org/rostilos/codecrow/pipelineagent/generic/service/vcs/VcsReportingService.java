package org.rostilos.codecrow.pipelineagent.generic.service.vcs;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.io.IOException;

/**
 * Interface for VCS-specific analysis result reporting.
 * Each VCS provider (Bitbucket, GitHub, GitLab) implements this to handle
 * provider-specific operations like posting comments and reports.
 */
public interface VcsReportingService {

    EVcsProvider getProvider();

    /**
     * Posts complete analysis results to the VCS platform.
     *
     * @param codeAnalysis      The code analysis results
     * @param project           The project entity
     * @param pullRequestNumber The PR number on the VCS platform
     * @param platformPrEntityId The internal PR entity ID
     */
    void postAnalysisResults(
            CodeAnalysis codeAnalysis,
            Project project,
            Long pullRequestNumber,
            Long platformPrEntityId
    ) throws IOException;
}
