package org.rostilos.codecrow.pipelineagent.webhookhandler.gitlab;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.pipelineagent.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.webhookhandler.AbstractWebhookHandler;
import org.rostilos.codecrow.pipelineagent.webhookhandler.WebhookHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Webhook handler for GitLab Merge Request events.
 */
@Component
public class GitLabMergeRequestWebhookHandler extends AbstractWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(GitLabMergeRequestWebhookHandler.class);
    
    private static final Set<String> SUPPORTED_MR_EVENTS = Set.of("merge_request");
    
    private static final Set<String> TRIGGERING_ACTIONS = Set.of(
        "open",
        "reopen",
        "update"
    );
    
    /** Comment marker for CodeCrow analysis responses */
    private static final String CODECROW_ANALYSIS_MARKER = "<!-- codecrow-analysis-comment -->";
    
    /** Placeholder message for auto-MR analysis */
    private static final String PLACEHOLDER_MR_ANALYSIS = """
        🔄 **CodeCrow is analyzing this MR...**
        
        This may take a few minutes depending on the size of the changes.
        This comment will be updated with the analysis results when complete.
        """;
    
    private final PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    private final VcsServiceFactory vcsServiceFactory;
    
    public GitLabMergeRequestWebhookHandler(
            PullRequestAnalysisProcessor pullRequestAnalysisProcessor,
            VcsServiceFactory vcsServiceFactory
    ) {
        this.pullRequestAnalysisProcessor = pullRequestAnalysisProcessor;
        this.vcsServiceFactory = vcsServiceFactory;
    }
    
    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITLAB;
    }
    
    @Override
    public boolean supportsEvent(String eventType) {
        return SUPPORTED_MR_EVENTS.contains(eventType);
    }
    
    @Override
    public WebhookResult handle(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer) {
        String eventType = payload.eventType();
        
        log.info("Handling GitLab MR event: {} for project {}", eventType, project.getId());
        
        // Check if the action is one we care about
        String action = null;
        if (payload.rawPayload().has("object_attributes")) {
            action = payload.rawPayload().get("object_attributes").path("action").asText(null);
        }
        
        if (action == null || !TRIGGERING_ACTIONS.contains(action)) {
            log.info("Ignoring GitLab MR event with action: {}", action);
            return WebhookResult.ignored("MR action '" + action + "' does not trigger analysis");
        }
        
        try {
            String validationError = validateProjectConnections(project);
            if (validationError != null) {
                log.warn("Project {} validation failed: {}", project.getId(), validationError);
                return WebhookResult.error(validationError);
            }
            
            if (!project.isPrAnalysisEnabled()) {
                log.info("MR analysis is disabled for project {}", project.getId());
                return WebhookResult.ignored("MR analysis is disabled for this project");
            }
            
            String targetBranch = payload.targetBranch();
            if (!shouldAnalyze(project, targetBranch, AnalysisType.PR_REVIEW)) {
                log.info("Skipping MR analysis: target branch '{}' does not match configured patterns for project {}", 
                        targetBranch, project.getId());
                return WebhookResult.ignored("Target branch '" + targetBranch + "' does not match configured analysis patterns");
            }
            
            return handleMergeRequestEvent(payload, project, eventConsumer);
        } catch (Exception e) {
            log.error("Error processing {} event for project {}", eventType, project.getId(), e);
            return WebhookResult.error("Processing failed: " + e.getMessage());
        }
    }


    
    private WebhookResult handleMergeRequestEvent(
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
            
            log.info("Processing MR analysis: project={}, MR={}, source={}, target={}, placeholderCommentId={}", 
                    project.getId(), request.pullRequestId, request.sourceBranchName, request.targetBranchName, placeholderCommentId);
            
            // Delegate to existing processor
            Map<String, Object> result = pullRequestAnalysisProcessor.process(
                    request, 
                    eventConsumer != null ? eventConsumer::accept : event -> {}, 
                    project
            );
            
            // Check if analysis failed
            if ("error".equals(result.get("status"))) {
                String errorMessage = (String) result.getOrDefault("message", "Analysis failed");
                return WebhookResult.error("MR analysis failed: " + errorMessage);
            }
            
            boolean cached = Boolean.TRUE.equals(result.get("cached"));
            if (cached) {
                return WebhookResult.success("Analysis result retrieved from cache", result);
            }
            
            return WebhookResult.success("MR analysis completed", result);
            
        } catch (Exception e) {
            log.error("MR analysis failed for project {}", project.getId(), e);
            // Try to update placeholder with error message
            if (placeholderCommentId != null) {
                try {
                    updatePlaceholderWithError(project, Long.parseLong(payload.pullRequestId()), placeholderCommentId, e.getMessage());
                } catch (Exception updateError) {
                    log.warn("Failed to update placeholder with error: {}", updateError.getMessage());
                }
            }
            return WebhookResult.error("MR analysis failed: " + e.getMessage());
        }
    }
    
    /**
     * Post a placeholder comment indicating CodeCrow is analyzing the MR.
     * Returns the comment ID for later updating.
     */
    private String postPlaceholderComment(Project project, Long mergeRequestIid) {
        try {
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(EVcsProvider.GITLAB);
            
            // Delete any previous analysis comments before posting placeholder
            try {
                int deleted = reportingService.deleteCommentsByMarker(project, mergeRequestIid, CODECROW_ANALYSIS_MARKER);
                if (deleted > 0) {
                    log.info("Deleted {} previous analysis comment(s) before posting placeholder", deleted);
                }
            } catch (Exception e) {
                log.warn("Failed to delete previous comments: {}", e.getMessage());
            }
            
            String commentId = reportingService.postComment(
                project, 
                mergeRequestIid, 
                PLACEHOLDER_MR_ANALYSIS, 
                CODECROW_ANALYSIS_MARKER
            );
            
            log.info("Posted placeholder comment {} for MR {}", commentId, mergeRequestIid);
            return commentId;
            
        } catch (Exception e) {
            log.error("Failed to post placeholder comment: {}", e.getMessage(), e);
            return null;
        }
    }
    
    /**
     * Update a placeholder comment with an error message.
     */
    private void updatePlaceholderWithError(Project project, Long mergeRequestIid, String commentId, String errorMessage) {
        try {
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(EVcsProvider.GITLAB);
            String errorContent = "⚠️ **CodeCrow Analysis Failed**\n\n" + errorMessage;
            reportingService.updateComment(project, mergeRequestIid, commentId, errorContent, CODECROW_ANALYSIS_MARKER);
            log.info("Updated placeholder comment {} with error message", commentId);
        } catch (Exception e) {
            log.error("Failed to update placeholder with error: {}", e.getMessage(), e);
        }
    }
}
