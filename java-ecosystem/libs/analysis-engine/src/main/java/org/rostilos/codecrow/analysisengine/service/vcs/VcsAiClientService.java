package org.rostilos.codecrow.analysisengine.service.vcs;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;

import java.security.GeneralSecurityException;
import java.util.List;
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

    /**
     * Builds an AI analysis request with full PR issue history.
     * 
     * @param project          The project being analyzed
     * @param request          The analysis process request
     * @param previousAnalysis Optional previous analysis for incremental analysis (used for delta diff calculation)
     * @param allPrAnalyses    All analyses for this PR, ordered by version DESC (for issue history)
     * @return The AI analysis request ready to be sent to the AI client
     */
    default AiAnalysisRequest buildAiAnalysisRequest(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses
    ) throws GeneralSecurityException {
        // Default implementation falls back to the previous method for backward compatibility
        return buildAiAnalysisRequest(project, request, previousAnalysis);
    }
}
