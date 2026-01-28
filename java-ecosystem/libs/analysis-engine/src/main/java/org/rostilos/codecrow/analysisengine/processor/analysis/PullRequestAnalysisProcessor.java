package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.events.analysis.AnalysisStartedEvent;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Generic service that handles pull request analysis.
 * Uses VCS-specific services via VcsServiceFactory for provider-specific operations.
 */
@Service
public class PullRequestAnalysisProcessor {
    private static final Logger log = LoggerFactory.getLogger(PullRequestAnalysisProcessor.class);

    private final CodeAnalysisService codeAnalysisService;
    private final PullRequestService pullRequestService;
    private final AiAnalysisClient aiAnalysisClient;
    private final VcsServiceFactory vcsServiceFactory;
    private final AnalysisLockService analysisLockService;
    private final RagOperationsService ragOperationsService;
    private final ApplicationEventPublisher eventPublisher;

    public PullRequestAnalysisProcessor(
            PullRequestService pullRequestService,
            CodeAnalysisService codeAnalysisService,
            AiAnalysisClient aiAnalysisClient,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService,
            @Autowired(required = false) RagOperationsService ragOperationsService,
            @Autowired(required = false) ApplicationEventPublisher eventPublisher
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.pullRequestService = pullRequestService;
        this.aiAnalysisClient = aiAnalysisClient;
        this.vcsServiceFactory = vcsServiceFactory;
        this.analysisLockService = analysisLockService;
        this.ragOperationsService = ragOperationsService;
        this.eventPublisher = eventPublisher;
    }

    public interface EventConsumer {
        void accept(Map<String, Object> event);
    }

    private EVcsProvider getVcsProvider(Project project) {
        // Use unified method to get effective VCS connection
        var vcsConnection = project.getEffectiveVcsConnection();
        if (vcsConnection != null) {
            return vcsConnection.getProviderType();
        }
        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    public Map<String, Object> process(
            PrProcessRequest request,
            EventConsumer consumer,
            Project project
    ) throws GeneralSecurityException {
        Instant startTime = Instant.now();
        String correlationId = java.util.UUID.randomUUID().toString();
        
        // Publish analysis started event
        publishAnalysisStartedEvent(project, request, correlationId);
        
        // Check if a lock was already acquired by the caller (e.g., webhook handler)
        // to prevent double-locking which causes unnecessary 2-minute waits
        String lockKey;
        boolean isPreAcquired = false;
        if (request.getPreAcquiredLockKey() != null && !request.getPreAcquiredLockKey().isBlank()) {
            lockKey = request.getPreAcquiredLockKey();
            isPreAcquired = true;
            log.info("Using pre-acquired lock: {} for project={}, PR={}", lockKey, project.getId(), request.getPullRequestId());
        } else {
            Optional<String> acquiredLock = analysisLockService.acquireLockWithWait(
                    project,
                    request.getSourceBranchName(),
                    AnalysisLockType.PR_ANALYSIS,
                    request.getCommitHash(),
                    request.getPullRequestId(),
                    consumer::accept
            );

            if (acquiredLock.isEmpty()) {
                String message = String.format(
                        "Failed to acquire lock after %d minutes for project=%s, PR=%d, branch=%s. Another analysis is still in progress.",
                        analysisLockService.getLockWaitTimeoutMinutes(),
                        project.getId(),
                        request.getPullRequestId(),
                        request.getSourceBranchName()
                );
                log.warn(message);
                
                // Publish failed event due to lock timeout
                publishAnalysisCompletedEvent(project, request, correlationId, startTime, 
                        AnalysisCompletedEvent.CompletionStatus.FAILED, 0, 0, "Lock acquisition timeout");
                
                throw new AnalysisLockedException(
                        AnalysisLockType.PR_ANALYSIS.name(),
                        request.getSourceBranchName(),
                        project.getId()
                );
            }
            lockKey = acquiredLock.get();
        }

        try {
            PullRequest pullRequest = pullRequestService.createOrUpdatePullRequest(
                    request.getProjectId(),
                    request.getPullRequestId(),
                    request.getCommitHash(),
                    request.getSourceBranchName(),
                    request.getTargetBranchName(),
                    project
            );

            EVcsProvider provider = getVcsProvider(project);
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);

            if (postAnalysisCacheIfExist(project, pullRequest, request.getCommitHash(), request.getPullRequestId(), reportingService, request.getPlaceholderCommentId())) {
                publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                        AnalysisCompletedEvent.CompletionStatus.SUCCESS, 0, 0, null);
                return Map.of("status", "cached", "cached", true);
            }

            // Get all previous analyses for this PR to provide full issue history to AI
            List<CodeAnalysis> allPrAnalyses = codeAnalysisService.getAllPrAnalyses(
                    project.getId(),
                    request.getPullRequestId()
            );
            
            // Get the most recent analysis for incremental diff calculation
            Optional<CodeAnalysis> previousAnalysis = allPrAnalyses.isEmpty() 
                    ? Optional.empty() 
                    : Optional.of(allPrAnalyses.get(0));

            // Ensure branch index exists for target branch if configured
            ensureRagIndexForTargetBranch(project, request.getTargetBranchName(), consumer);

            VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);
            AiAnalysisRequest aiRequest = aiClientService.buildAiAnalysisRequest(
                    project, request, previousAnalysis, allPrAnalyses);

            Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiRequest, event -> {
                try {
                    log.debug("Received event from AI client: type={}", event.get("type"));
                    consumer.accept(event);
                    log.debug("Event forwarded to consumer successfully");
                } catch (Exception ex) {
                    log.error("Event consumer failed: {}", ex.getMessage(), ex);
                }
            });

            CodeAnalysis newAnalysis = codeAnalysisService.createAnalysisFromAiResponse(
                    project,
                    aiResponse,
                    request.getPullRequestId(),
                    request.getTargetBranchName(),
                    request.getSourceBranchName(),
                    request.getCommitHash(),
                    request.getPrAuthorId(),
                    request.getPrAuthorUsername()
            );
            
            int issuesFound = newAnalysis.getIssues() != null ? newAnalysis.getIssues().size() : 0;

            try {
                reportingService.postAnalysisResults(
                        newAnalysis,
                        project,
                        request.getPullRequestId(),
                        pullRequest.getId(),
                        request.getPlaceholderCommentId()
                );
            } catch (IOException e) {
                log.error("Failed to post analysis results to VCS: {}", e.getMessage(), e);
                consumer.accept(Map.of(
                        "type", "warning",
                        "message", "Analysis completed but failed to post results to VCS: " + e.getMessage()
                ));
            }
            
            // Publish successful completion event
            publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                    AnalysisCompletedEvent.CompletionStatus.SUCCESS, issuesFound, 
                    aiRequest.getChangedFiles() != null ? aiRequest.getChangedFiles().size() : 0, null);

            return aiResponse;
        } catch (IOException e) {
            log.error("IOException during PR analysis: {}", e.getMessage(), e);
            consumer.accept(Map.of(
                    "type", "error",
                    "message", "Analysis failed due to I/O error: " + e.getMessage()
            ));
            
            // Publish failed event
            publishAnalysisCompletedEvent(project, request, correlationId, startTime,
                    AnalysisCompletedEvent.CompletionStatus.FAILED, 0, 0, e.getMessage());
            
            return Map.of("status", "error", "message", e.getMessage());
        } finally {
            if (!isPreAcquired) {
                analysisLockService.releaseLock(lockKey);
            }
        }
    }

    protected boolean postAnalysisCacheIfExist(
            Project project, 
            PullRequest pullRequest, 
            String commitHash, 
            Long prId,
            VcsReportingService reportingService,
            String placeholderCommentId
    ) {
        Optional<CodeAnalysis> cachedAnalysis = codeAnalysisService.getCodeAnalysisCache(
                project.getId(),
                commitHash,
                prId
        );

        if (cachedAnalysis.isPresent()) {
            try {
                reportingService.postAnalysisResults(
                        cachedAnalysis.get(),
                        project,
                        prId,
                        pullRequest.getId(),
                        placeholderCommentId
                );
            } catch (IOException e) {
                log.error("Failed to post cached analysis results to VCS: {}", e.getMessage(), e);
            }
            return true;
        }
        return false;
    }
    
    /**
     * Ensures RAG index is up-to-date for the PR target branch.
     * 
     * For PRs targeting the main branch:
     * - Checks if the main RAG index commit matches the current target branch HEAD
     * - If outdated, performs incremental update before analysis
     * 
     * For PRs targeting non-main branches with multi-branch enabled:
     * - First ensures the main index is up to date
     * - Then ensures branch index exists and is up to date for the target branch
     * 
     * This ensures analysis always uses the most current codebase context.
     */
    private void ensureRagIndexForTargetBranch(Project project, String targetBranch, EventConsumer consumer) {
        if (ragOperationsService == null) {
            log.debug("RagOperationsService not available - skipping RAG index check for target branch");
            return;
        }
        
        try {
            boolean ready = ragOperationsService.ensureRagIndexUpToDate(
                    project, 
                    targetBranch, 
                    consumer::accept
            );
            if (ready) {
                log.info("RAG index ensured up-to-date for PR target branch: project={}, branch={}", 
                        project.getId(), targetBranch);
            }
        } catch (Exception e) {
            log.warn("Failed to ensure RAG index up-to-date for target branch (non-critical): project={}, branch={}, error={}",
                    project.getId(), targetBranch, e.getMessage());
        }
    }
    
    /**
     * Publishes an AnalysisStartedEvent for PR analysis.
     */
    private void publishAnalysisStartedEvent(Project project, PrProcessRequest request, String correlationId) {
        if (eventPublisher == null) {
            return;
        }
        try {
            AnalysisStartedEvent event = new AnalysisStartedEvent(
                    this,
                    correlationId,
                    project.getId(),
                    project.getName(),
                    AnalysisStartedEvent.AnalysisType.PULL_REQUEST,
                    request.getSourceBranchName(),
                    null // jobId not available at this level
            );
            eventPublisher.publishEvent(event);
            log.debug("Published AnalysisStartedEvent for PR analysis: project={}, pr={}", 
                    project.getId(), request.getPullRequestId());
        } catch (Exception e) {
            log.warn("Failed to publish AnalysisStartedEvent: {}", e.getMessage());
        }
    }
    
    /**
     * Publishes an AnalysisCompletedEvent for PR analysis.
     */
    private void publishAnalysisCompletedEvent(Project project, PrProcessRequest request, 
            String correlationId, Instant startTime,
            AnalysisCompletedEvent.CompletionStatus status, int issuesFound, 
            int filesAnalyzed, String errorMessage) {
        if (eventPublisher == null) {
            return;
        }
        try {
            Duration duration = Duration.between(startTime, Instant.now());
            Map<String, Object> metrics = new HashMap<>();
            metrics.put("prNumber", request.getPullRequestId());
            metrics.put("targetBranch", request.getTargetBranchName());
            metrics.put("sourceBranch", request.getSourceBranchName());
            
            AnalysisCompletedEvent event = new AnalysisCompletedEvent(
                    this,
                    correlationId,
                    project.getId(),
                    null, // jobId not available at this level
                    status,
                    duration,
                    issuesFound,
                    filesAnalyzed,
                    errorMessage,
                    metrics
            );
            eventPublisher.publishEvent(event);
            log.debug("Published AnalysisCompletedEvent for PR analysis: project={}, pr={}, status={}, duration={}ms", 
                    project.getId(), request.getPullRequestId(), status, duration.toMillis());
        } catch (Exception e) {
            log.warn("Failed to publish AnalysisCompletedEvent: {}", e.getMessage());
        }
    }
}
