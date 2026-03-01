package org.rostilos.codecrow.analysisengine.service.vcs;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
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
     * Builds AI analysis requests with VCS-specific data.
     * Large diffs will be chunked into multiple requests.
     *
     * @param project          The project being analyzed
     * @param request          The analysis process request
     * @param previousAnalysis Optional previous analysis for incremental analysis
     * @return List of AI analysis requests ready to be sent to the AI client
     */
    List<AiAnalysisRequest> buildAiAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis) throws GeneralSecurityException;

    /**
     * Builds AI analysis requests with full PR issue history.
     * Large diffs will be chunked into multiple requests.
     * 
     * @param project          The project being analyzed
     * @param request          The analysis process request
     * @param previousAnalysis Optional previous analysis for incremental analysis
     *                         (used for delta diff calculation)
     * @param allPrAnalyses    All analyses for this PR, ordered by version DESC
     *                         (for issue history)
     * @return List of AI analysis requests ready to be sent to the AI client
     */
    default List<AiAnalysisRequest> buildAiAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses) throws GeneralSecurityException {
        // Default implementation falls back to the previous method for backward
        // compatibility
        return buildAiAnalysisRequests(project, request, previousAnalysis);
    }

    /**
     * Builds AI analysis requests for branch reconciliation using pre-built issue
     * DTOs.
     * <p>
     * Unlike {@link #buildAiAnalysisRequests} which converts from
     * {@link CodeAnalysis} entities,
     * this method accepts pre-built {@link AiRequestPreviousIssueDTO} objects —
     * typically
     * constructed from independent {@code BranchIssue} data via
     * {@link AiRequestPreviousIssueDTO#fromBranchIssue}.
     * <p>
     * This avoids {@code LazyInitializationException} when origin
     * {@code CodeAnalysisIssue}
     * proxies are accessed outside a Hibernate session during branch
     * reconciliation.
     *
     * @param project        The project being analyzed
     * @param request        The analysis process request (must be a
     *                       BranchProcessRequest)
     * @param previousIssues Pre-built DTOs describing the issues to reconcile
     * @return List of AI analysis requests ready to be sent to the AI client
     */
    default List<AiAnalysisRequest> buildAiAnalysisRequestsForBranchReconciliation(
            Project project,
            AnalysisProcessRequest request,
            List<AiRequestPreviousIssueDTO> previousIssues) throws GeneralSecurityException {
        return buildAiAnalysisRequestsForBranchReconciliation(project, request, previousIssues, null);
    }

    /**
     * Builds AI analysis requests for branch reconciliation with pre-fetched file
     * contents.
     * When {@code fileContents} is non-null and non-empty, the Python inference
     * orchestrator
     * will use them directly instead of spawning an MCP agent to fetch files via
     * VCS tool calls.
     *
     * @param project        The project being analyzed
     * @param request        The analysis process request (must be a
     *                       BranchProcessRequest)
     * @param previousIssues Pre-built DTOs describing the issues to reconcile
     * @param fileContents   Map of filePath → full file content (pre-fetched by
     *                       Java)
     * @return List of AI analysis requests ready to be sent to the AI client
     */
    default List<AiAnalysisRequest> buildAiAnalysisRequestsForBranchReconciliation(
            Project project,
            AnalysisProcessRequest request,
            List<AiRequestPreviousIssueDTO> previousIssues,
            java.util.Map<String, String> fileContents) throws GeneralSecurityException {
        // Default: delegate to standard method without previous issues.
        // Providers should override to inject previousIssues and fileContents into the
        // builder.
        return buildAiAnalysisRequests(project, request, Optional.empty());
    }

    /**
     * Builds AI analysis requests for direct push (hybrid branch analysis).
     * <p>
     * Unlike PR analysis, this does NOT fetch a PR diff — it uses the commit range
     * diff
     * between the analyzed ancestor and HEAD. The builder injects the raw diff,
     * enrichment
     * data, and RAG context from the branch (not from a PR target branch).
     * <p>
     * Unlike branch reconciliation, this produces FULL analysis requests (with
     * diff, file
     * metadata, enrichment) — not just a list of existing issues to verify.
     *
     * @param project      The project being analyzed
     * @param request      The analysis process request (must be a
     *                     BranchProcessRequest)
     * @param rawDiff      The commit range diff (base..HEAD)
     * @param fileContents Map of filePath → full file content
     * @param changedFiles List of changed file paths parsed from the diff
     * @return List of AI analysis requests ready to be sent to the AI client
     */
    default List<AiAnalysisRequest> buildDirectPushAnalysisRequests(
            Project project,
            AnalysisProcessRequest request,
            String rawDiff,
            java.util.Map<String, String> fileContents,
            java.util.List<String> changedFiles) throws GeneralSecurityException {
        // Default: fall back to standard branch analysis.
        // Providers should override to build a full PR-like request from commit range
        // diff.
        return buildAiAnalysisRequests(project, request, Optional.empty());
    }
}
