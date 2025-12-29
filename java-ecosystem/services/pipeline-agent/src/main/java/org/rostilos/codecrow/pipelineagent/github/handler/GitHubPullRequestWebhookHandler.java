package org.rostilos.codecrow.pipelineagent.github.handler;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.util.BranchPatternMatcher;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrReconciliationRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.processor.analysis.PrReconciliationProcessor;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhook.handler.WebhookHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;

/**
 * Webhook handler for GitHub Pull Request events.
 */
@Component
public class GitHubPullRequestWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(GitHubPullRequestWebhookHandler.class);
    
    private static final Set<String> SUPPORTED_PR_EVENTS = Set.of("pull_request");
    
    private static final Set<String> TRIGGERING_ACTIONS = Set.of(
        "opened",
        "synchronize",
        "reopened"
    );
    
    /** Comment marker for CodeCrow analysis responses */
    private static final String CODECROW_ANALYSIS_MARKER = "<!-- codecrow-analysis-comment -->";
    
    /** Placeholder message for auto-PR analysis */
    private static final String PLACEHOLDER_PR_ANALYSIS = """
        🔄 **CodeCrow is analyzing this PR...**
        
        This may take a few minutes depending on the size of the changes.
        This comment will be updated with the analysis results when complete.
        """;
    
    private final PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    private final PrReconciliationProcessor prReconciliationProcessor;
    private final VcsServiceFactory vcsServiceFactory;
    
    public GitHubPullRequestWebhookHandler(
            PullRequestAnalysisProcessor pullRequestAnalysisProcessor,
            PrReconciliationProcessor prReconciliationProcessor,
            VcsServiceFactory vcsServiceFactory
    ) {
        this.pullRequestAnalysisProcessor = pullRequestAnalysisProcessor;
        this.prReconciliationProcessor = prReconciliationProcessor;
        this.vcsServiceFactory = vcsServiceFactory;
    }
    
    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITHUB;
    }
    
    @Override
    public boolean supportsEvent(String eventType) {
        return SUPPORTED_PR_EVENTS.contains(eventType);
    }
    
    @Override
    public WebhookResult handle(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer) {
        String eventType = payload.eventType();
        
        log.info("Handling GitHub PR event: {} for project {}", eventType, project.getId());
        
        // Check if the action is one we care about
        String action = payload.rawPayload().has("action") 
                ? payload.rawPayload().get("action").asText() 
                : null;
        
        if (action == null || !TRIGGERING_ACTIONS.contains(action)) {
            log.info("Ignoring GitHub PR event with action: {}", action);
            return WebhookResult.ignored("PR action '" + action + "' does not trigger analysis");
        }
        
        try {
            String validationError = validateProjectConnections(project);
            if (validationError != null) {
                log.warn("Project {} validation failed: {}", project.getId(), validationError);
                return WebhookResult.error(validationError);
            }
            
            if (!project.isPrAnalysisEnabled()) {
                log.info("PR analysis is disabled for project {}", project.getId());
                return WebhookResult.ignored("PR analysis is disabled for this project");
            }
            
            String targetBranch = payload.targetBranch();
            if (!shouldAnalyzePullRequest(project, targetBranch)) {
                log.info("Skipping PR analysis: target branch '{}' does not match configured patterns for project {}", 
                        targetBranch, project.getId());
                return WebhookResult.ignored("Target branch '" + targetBranch + "' does not match configured analysis patterns");
            }
            
            return handlePullRequestEvent(payload, project, eventConsumer);
        } catch (Exception e) {
            log.error("Error processing {} event for project {}", eventType, project.getId(), e);
            return WebhookResult.error("Processing failed: " + e.getMessage());
        }
    }
    
    private String validateProjectConnections(Project project) {
        boolean hasVcsConnection = project.getVcsBinding() != null ||
                (project.getVcsRepoBinding() != null && project.getVcsRepoBinding().getVcsConnection() != null);
        
        if (!hasVcsConnection) {
            return "VCS connection is not configured for project: " + project.getId();
        }

        if (project.getAiBinding() == null) {
            return "AI connection is not configured for project: " + project.getId();
        }
        
        return null;
    }
    
    /**
     * Check if a PR's target branch matches the configured analysis patterns.
     * Note: isPrAnalysisEnabled() check is done in the handle() method before this is called.
     */
    private boolean shouldAnalyzePullRequest(Project project, String targetBranch) {
        if (project.getConfiguration() == null) {
            return true;
        }
        
        ProjectConfig.BranchAnalysisConfig branchConfig = project.getConfiguration().branchAnalysis();
        if (branchConfig == null) {
            return true;
        }
        
        List<String> prTargetBranches = branchConfig.prTargetBranches();
        return BranchPatternMatcher.shouldAnalyze(targetBranch, prTargetBranches);
    }
    
    private WebhookResult handlePullRequestEvent(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        String placeholderCommentId = null;
        
        try {
            // Post placeholder comment immediately to show analysis has started
            placeholderCommentId = postPlaceholderComment(project, Long.parseLong(payload.pullRequestId()));
            
            // Convert WebhookPayload to PrProcessRequest
            PrProcessRequest request = new PrProcessRequest();
            request.projectId = project.getId();
            request.pullRequestId = Long.parseLong(payload.pullRequestId());
            request.sourceBranchName = payload.sourceBranch();
            request.targetBranchName = payload.targetBranch();
            request.commitHash = payload.commitHash();
            request.analysisType = AnalysisType.PR_REVIEW;
            request.placeholderCommentId = placeholderCommentId;
            
            log.info("Processing PR analysis: project={}, PR={}, source={}, target={}, placeholderCommentId={}", 
                    project.getId(), request.pullRequestId, request.sourceBranchName, request.targetBranchName, placeholderCommentId);
            
            // Start PR Reconciliation in parallel (checks if existing branch issues might be fixed)
            CompletableFuture<Void> reconciliationFuture = startPrReconciliationAsync(project, payload);
            
            // Delegate to existing processor - Consumer<Map<String, Object>> is compatible
            // with PullRequestAnalysisProcessor.EventConsumer functional interface
            Map<String, Object> result = pullRequestAnalysisProcessor.process(
                    request, 
                    eventConsumer != null ? eventConsumer::accept : event -> {}, 
                    project
            );
            
            // Wait for reconciliation to complete (it runs in parallel, shouldn't block long)
            try {
                reconciliationFuture.join();
            } catch (Exception e) {
                log.warn("PR reconciliation completed with error (non-blocking): {}", e.getMessage());
            }
            
            boolean cached = Boolean.TRUE.equals(result.get("cached"));
            if (cached) {
                return WebhookResult.success("Analysis result retrieved from cache", result);
            }
            
            return WebhookResult.success("PR analysis completed", result);
            
        } catch (Exception e) {
            log.error("PR analysis failed for project {}", project.getId(), e);
            // Try to update placeholder with error message
            if (placeholderCommentId != null) {
                try {
                    updatePlaceholderWithError(project, Long.parseLong(payload.pullRequestId()), placeholderCommentId, e.getMessage());
                } catch (Exception updateError) {
                    log.warn("Failed to update placeholder with error: {}", updateError.getMessage());
                }
            }
            return WebhookResult.error("PR analysis failed: " + e.getMessage());
        }
    }
    
    /**
     * Start PR Reconciliation analysis asynchronously.
     * This checks if existing branch issues might be fixed by this PR.
     * Runs in parallel with the main PR analysis to avoid blocking.
     */
    private CompletableFuture<Void> startPrReconciliationAsync(Project project, WebhookPayload payload) {
        return CompletableFuture.runAsync(() -> {
            try {
                PrReconciliationRequest reconciliationRequest = new PrReconciliationRequest();
                reconciliationRequest.projectId = project.getId();
                reconciliationRequest.pullRequestId = Long.parseLong(payload.pullRequestId());
                reconciliationRequest.sourceBranchName = payload.sourceBranch();
                reconciliationRequest.targetBranchName = payload.targetBranch();
                reconciliationRequest.commitHash = payload.commitHash();
                
                log.info("Starting PR reconciliation: project={}, PR={}, target={}",
                        project.getId(), payload.pullRequestId(), payload.targetBranch());
                
                Map<String, Object> reconciliationResult = prReconciliationProcessor.process(
                        reconciliationRequest,
                        event -> log.debug("PR reconciliation event: {}", event)
                );
                
                int potentiallyResolved = (int) reconciliationResult.getOrDefault("potentiallyResolvedCount", 0);
                log.info("PR reconciliation completed: project={}, PR={}, potentiallyResolved={}",
                        project.getId(), payload.pullRequestId(), potentiallyResolved);
                
            } catch (Exception e) {
                log.warn("PR reconciliation failed (non-blocking): project={}, PR={}, error={}",
                        project.getId(), payload.pullRequestId(), e.getMessage());
            }
        });
    }
    
    /**
     * Post a placeholder comment indicating CodeCrow is analyzing the PR.
     * Returns the comment ID for later updating.
     */
    private String postPlaceholderComment(Project project, Long pullRequestNumber) {
        try {
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(EVcsProvider.GITHUB);
            
            // Delete any previous analysis comments before posting placeholder
            try {
                int deleted = reportingService.deleteCommentsByMarker(project, pullRequestNumber, CODECROW_ANALYSIS_MARKER);
                if (deleted > 0) {
                    log.info("Deleted {} previous analysis comment(s) before posting placeholder", deleted);
                }
            } catch (Exception e) {
                log.warn("Failed to delete previous comments: {}", e.getMessage());
            }
            
            String commentId = reportingService.postComment(
                project, 
                pullRequestNumber, 
                PLACEHOLDER_PR_ANALYSIS, 
                CODECROW_ANALYSIS_MARKER
            );
            
            log.info("Posted placeholder comment {} for PR {}", commentId, pullRequestNumber);
            return commentId;
            
        } catch (Exception e) {
            log.error("Failed to post placeholder comment: {}", e.getMessage(), e);
            // Don't fail the analysis if placeholder posting fails
            return null;
        }
    }
    
    /**
     * Update a placeholder comment with an error message.
     */
    private void updatePlaceholderWithError(Project project, Long pullRequestNumber, String commentId, String errorMessage) {
        try {
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(EVcsProvider.GITHUB);
            String errorContent = "⚠️ **CodeCrow Analysis Failed**\n\n" + errorMessage;
            reportingService.updateComment(project, pullRequestNumber, commentId, errorContent, CODECROW_ANALYSIS_MARKER);
            log.info("Updated placeholder comment {} with error message", commentId);
        } catch (Exception e) {
            log.error("Failed to update placeholder with error: {}", e.getMessage(), e);
        }
    }
}
