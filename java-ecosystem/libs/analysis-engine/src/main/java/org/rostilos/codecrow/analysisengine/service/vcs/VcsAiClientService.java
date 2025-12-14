package org.rostilos.codecrow.analysisengine.service.vcs;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;

import java.security.GeneralSecurityException;
import java.util.Optional;

/**
 * Interface for VCS-specific AI analysis request building.
 * Each VCS provider (Bitbucket, GitHub, GitLab) implements this to handle
 * provider-specific operations like fetching PR diffs and metadata.
 */
public interface VcsAiClientService {

    EVcsProvider getProvider();

    /**
     * Builds an AI analysis request with VCS-specific data.
     *
     * @param project          The project being analyzed
     * @param request          The analysis process request
     * @param previousAnalysis Optional previous analysis for incremental analysis
     * @return The AI analysis request ready to be sent to the AI client
     */
    AiAnalysisRequest buildAiAnalysisRequest(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis
    ) throws GeneralSecurityException;
}
