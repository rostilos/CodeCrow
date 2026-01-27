package org.rostilos.codecrow.pipelineagent.generic.processor;

import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.exception.DiffTooLargeException;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.slf4j.Logger;

import java.io.IOException;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Service for processing webhooks asynchronously within a transactional context.
 * This ensures Hibernate sessions are available for lazy loading.
 */
@Service
public class WebhookAsyncProcessor {
    
    private static final Logger log = LoggerFactory.getLogger(WebhookAsyncProcessor.class);
    
    /** Comment markers for CodeCrow command responses */
    private static final String CODECROW_COMMAND_MARKER = "<!-- codecrow-command-response -->";
    private static final String CODECROW_SUMMARY_MARKER = "<!-- codecrow-summary -->";
    private static final String CODECROW_REVIEW_MARKER = "<!-- codecrow-review -->";
    
    /** Placeholder messages for commands */
    private static final String PLACEHOLDER_ANALYZE = """
        🔄 **CodeCrow is analyzing this PR...**
        
        This may take a few minutes depending on the size of the changes.
        I'll update this comment with the results when the analysis is complete.
        """;
    
    private static final String PLACEHOLDER_SUMMARIZE = """
        🔄 **CodeCrow is generating a summary...**
        
        I'm analyzing the changes and creating diagrams.
        This comment will be updated with the summary when ready.
        """;
    
    private static final String PLACEHOLDER_REVIEW = """
        🔄 **CodeCrow is reviewing this PR...**
        
        I'm examining the code changes for potential issues.
        This comment will be updated with the review results when complete.
        """;
    
    private static final String PLACEHOLDER_ASK = """
        🔄 **CodeCrow is processing your question...**
        
        I'm analyzing the context to provide a helpful answer.
        """;
    
    private static final String PLACEHOLDER_DEFAULT = """
        🔄 **CodeCrow is processing...**
        
        Please wait while I complete this task.
        """;
    
    private final ProjectRepository projectRepository;
    private final JobService jobService;
    private final VcsServiceFactory vcsServiceFactory;
    
    public WebhookAsyncProcessor(
            ProjectRepository projectRepository,
            JobService jobService,
            VcsServiceFactory vcsServiceFactory
    ) {
        this.projectRepository = projectRepository;
        this.jobService = jobService;
        this.vcsServiceFactory = vcsServiceFactory;
    }
    
    /**
     * Process a webhook asynchronously.
     * This method uses a transaction to ensure lazy associations can be loaded.
     * Inner operations like failJob use REQUIRES_NEW which creates nested transactions as needed.
     */
    @Async("webhookExecutor")
    @Transactional
    public void processWebhookAsync(
            EVcsProvider provider,
            Long projectId,
            WebhookPayload payload,
            WebhookHandler handler,
            Job job
    ) {
        String placeholderCommentId = null;
        Project project = null;
        
        // Store job external ID for re-fetching - the passed Job entity is detached
        // since it was created in the HTTP request transaction which has already committed
        String jobExternalId = job.getExternalId();
        
        // Declare managed job reference that will be set after re-fetching
        // This needs to be accessible in catch blocks for error handling
        Job managedJob = null;
        
        try {
            // Re-fetch project to ensure all lazy associations are available
            project = projectRepository.findById(projectId)
                .orElseThrow(() -> new IllegalStateException("Project not found: " + projectId));
            
            // Initialize lazy associations we'll need
            initializeProjectAssociations(project);
            
            // Re-fetch the job by external ID to get a managed entity in the current context
            // This is necessary because the Job was created in the HTTP request transaction
            // which has already committed by the time this async method runs
            managedJob = jobService.findByExternalIdOrThrow(jobExternalId);
            
            // Create final reference for use in lambda
            final Job jobForLambda = managedJob;
            
            jobService.startJob(managedJob);
            
            // Post placeholder comment immediately if this is a CodeCrow command on a PR
            if (payload.hasCodecrowCommand() && payload.pullRequestId() != null) {
                placeholderCommentId = postPlaceholderComment(provider, project, payload, managedJob);
            }
            
            // Store placeholder ID for use in result posting
            final String finalPlaceholderCommentId = placeholderCommentId;
            
            // Create event consumer that logs to job
            WebhookHandler.WebhookResult result = handler.handle(payload, project, event -> {
                String message = (String) event.getOrDefault("message", "Processing...");
                String state = (String) event.getOrDefault("state", "processing");
                jobService.info(jobForLambda, state, message);
            });
            
            // Check if the webhook was ignored (e.g., branch not matching pattern, analysis disabled)
            if ("ignored".equals(result.status())) {
                log.info("Webhook ignored: {}", result.message());
                // Delete placeholder if we posted one for an ignored command
                if (finalPlaceholderCommentId != null) {
                    deletePlaceholderComment(provider, project, payload, finalPlaceholderCommentId);
                }
                // Delete the job entirely - don't clutter DB with ignored webhooks
                // If deletion fails, skip the job instead
                try {
                    jobService.deleteIgnoredJob(managedJob, result.message());
                } catch (Exception deleteError) {
                    log.warn("Failed to delete ignored job {}, skipping instead: {}", 
                            managedJob.getExternalId(), deleteError.getMessage());
                    try {
                        jobService.skipJob(managedJob, result.message());
                    } catch (Exception skipError) {
                        log.error("Failed to skip job {}: {}", managedJob.getExternalId(), skipError.getMessage());
                    }
                }
                return;
            }
            
            if (result.success()) {
                // Post result to VCS if there's content to post
                postResultToVcs(provider, project, payload, result, finalPlaceholderCommentId, managedJob);
                
                if (result.data().containsKey("analysisId")) {
                    Long analysisId = ((Number) result.data().get("analysisId")).longValue();
                    jobService.info(managedJob, "complete", "Analysis completed. Analysis ID: " + analysisId);
                }
                jobService.completeJob(managedJob);
            } else {
                // Post error to VCS (update placeholder if exists) - but ensure failJob is always called
                try {
                    postErrorToVcs(provider, project, payload, result.message(), finalPlaceholderCommentId, managedJob);
                } catch (Exception postError) {
                    log.error("Failed to post error to VCS: {}", postError.getMessage());
                }
                // Always mark the job as failed, even if posting to VCS failed
                jobService.failJob(managedJob, result.message());
            }
            
        } catch (DiffTooLargeException diffEx) {
            // Handle diff too large - this is a soft skip, not an error
            log.warn("Diff too large for analysis - skipping: {}", diffEx.getMessage());
            
            // Re-fetch job if not yet fetched
            if (managedJob == null) {
                try {
                    managedJob = jobService.findByExternalIdOrThrow(jobExternalId);
                } catch (Exception fetchError) {
                    log.error("Failed to fetch job {} for skip operation: {}", jobExternalId, fetchError.getMessage());
                    return;
                }
            }
            
            String skipMessage = String.format(
                "⚠️ **Analysis Skipped - PR Too Large**\n\n" +
                "This PR's diff exceeds the configured token limit:\n" +
                "- **Estimated tokens:** %,d\n" +
                "- **Maximum allowed:** %,d (%.1f%% of limit)\n\n" +
                "To analyze this PR, consider:\n" +
                "1. Breaking it into smaller PRs\n" +
                "2. Increasing the token limit in project settings\n" +
                "3. Using `/codecrow analyze` command on specific commits",
                diffEx.getEstimatedTokens(),
                diffEx.getMaxAllowedTokens(),
                diffEx.getUtilizationPercentage()
            );
            
            try {
                if (project == null) {
                    project = projectRepository.findById(projectId).orElse(null);
                }
                if (project != null) {
                    initializeProjectAssociations(project);
                    postInfoToVcs(provider, project, payload, skipMessage, placeholderCommentId, managedJob);
                }
            } catch (Exception postError) {
                log.error("Failed to post skip message to VCS: {}", postError.getMessage());
            }
            
            try {
                jobService.skipJob(managedJob, "Diff too large: " + diffEx.getEstimatedTokens() + " tokens > " + diffEx.getMaxAllowedTokens() + " limit");
            } catch (Exception skipError) {
                log.error("Failed to skip job: {}", skipError.getMessage());
            }
            
        } catch (AnalysisLockedException lockEx) {
            // Handle lock acquisition failure - mark job as failed
            log.warn("Lock acquisition failed for analysis: {}", lockEx.getMessage());
            
            // Re-fetch job if not yet fetched
            if (managedJob == null) {
                try {
                    managedJob = jobService.findByExternalIdOrThrow(jobExternalId);
                } catch (Exception fetchError) {
                    log.error("Failed to fetch job {} for fail operation: {}", jobExternalId, fetchError.getMessage());
                    return;
                }
            }
            
            String failMessage = String.format(
                "⚠️ **Analysis Failed - Resource Locked**\n\n" +
                "Could not acquire analysis lock after timeout:\n" +
                "- **Lock type:** %s\n" +
                "- **Branch:** %s\n" +
                "- **Project:** %d\n\n" +
                "Another analysis may be in progress. Please try again later.",
                lockEx.getLockType(),
                lockEx.getBranchName(),
                lockEx.getProjectId()
            );
            
            try {
                if (project == null) {
                    project = projectRepository.findById(projectId).orElse(null);
                }
                if (project != null) {
                    initializeProjectAssociations(project);
                    postErrorToVcs(provider, project, payload, failMessage, placeholderCommentId, managedJob);
                }
            } catch (Exception postError) {
                log.error("Failed to post lock error to VCS: {}", postError.getMessage());
            }
            
            try {
                jobService.failJob(managedJob, "Lock acquisition timeout: " + lockEx.getMessage());
            } catch (Exception failError) {
                log.error("Failed to fail job: {}", failError.getMessage());
            }
            
        } catch (Exception e) {
            // Re-fetch job if not yet fetched
            if (managedJob == null) {
                try {
                    managedJob = jobService.findByExternalIdOrThrow(jobExternalId);
                } catch (Exception fetchError) {
                    log.error("Failed to fetch job {} for fail operation: {}", jobExternalId, fetchError.getMessage());
                    log.error("Original error processing webhook", e);
                    return;
                }
            }
            
            log.error("Error processing webhook for job {}", managedJob.getExternalId(), e);
            
            try {
                if (project == null) {
                    project = projectRepository.findById(projectId).orElse(null);
                }
                if (project != null) {
                    initializeProjectAssociations(project);
                    postErrorToVcs(provider, project, payload, "Processing failed: " + e.getMessage(), placeholderCommentId, managedJob);
                }
            } catch (Exception postError) {
                log.error("Failed to post error to VCS: {}", postError.getMessage());
            }
            
            try {
                jobService.failJob(managedJob, "Processing failed: " + e.getMessage());
            } catch (Exception failError) {
                log.error("Failed to mark job as failed: {}", failError.getMessage());
            }
        }
    }
    
    /**
     * Initialize lazy associations that will be needed for VCS operations.
     */
    private void initializeProjectAssociations(Project project) {
        // Force initialization of VCS connections using unified accessor
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null) {
            var vcsConn = vcsInfo.getVcsConnection();
            if (vcsConn != null) {
                // Touch to initialize
                vcsConn.getConnectionType();
                vcsConn.getProviderType();
            }
        }
    }
    
    /**
     * Post the result of a command to VCS as a comment.
     */
    private void postResultToVcs(EVcsProvider provider, Project project, WebhookPayload payload, 
                                  WebhookHandler.WebhookResult result, String placeholderCommentId, Job job) {
        try {
            // Only post for command results that have content
            String commandType = (String) result.data().get("commandType");
            log.info("postResultToVcs: commandType={}, resultData={}, placeholderCommentId={}", 
                commandType, result.data().keySet(), placeholderCommentId);
            
            if (commandType == null) {
                log.debug("No commandType in result, skipping VCS post");
                // Delete placeholder if we posted one but got no result
                if (placeholderCommentId != null) {
                    deletePlaceholderComment(provider, project, payload, placeholderCommentId);
                }
                return;
            }
            
            String content = (String) result.data().get("content");
            if (content == null || content.isBlank()) {
                log.debug("No content in result, skipping VCS post");
                // Delete placeholder if we posted one but got no content
                if (placeholderCommentId != null) {
                    deletePlaceholderComment(provider, project, payload, placeholderCommentId);
                }
                return;
            }
            
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            
            if (payload.pullRequestId() != null) {
                // Handle "ask" differently - reply to the comment, don't delete anything
                if ("ask".equalsIgnoreCase(commandType)) {
                    log.info("Posting ASK response as reply");
                    postAskReply(reportingService, project, payload, content, placeholderCommentId, job);
                } else {
                    // For other commands (summarize, review, analyze) - update placeholder or post new
                    log.info("Posting {} response (placeholderCommentId={})", commandType, placeholderCommentId);
                    postWithMarker(reportingService, project, payload, content, commandType, placeholderCommentId, job);
                }
            }
            
        } catch (Exception e) {
            log.error("Failed to post result to VCS: {}", e.getMessage(), e);
            jobService.warn(job, "post_failed", "Failed to post result to VCS: " + e.getMessage());
        }
    }
    
    /**
     * Post an "ask" response as a reply to the triggering comment.
     * If placeholderCommentId is provided, deletes the placeholder and posts as reply.
     */
    private void postAskReply(VcsReportingService reportingService, Project project, 
                               WebhookPayload payload, String content, String placeholderCommentId, Job job) throws IOException {
        // Delete the placeholder comment if one was posted - for ASK, we reply to the original comment
        if (placeholderCommentId != null) {
            try {
                reportingService.deleteComment(project, Long.parseLong(payload.pullRequestId()), placeholderCommentId);
                log.info("Deleted placeholder comment {} before posting ask reply", placeholderCommentId);
            } catch (Exception e) {
                log.warn("Failed to delete placeholder comment {}: {}", placeholderCommentId, e.getMessage());
            }
        }
        
        String parentCommentId = null;
        String authorUsername = null;
        String originalBody = null;
        
        // Get the parent comment info from the payload
        if (payload.commentData() != null) {
            parentCommentId = payload.commentData().commentId();
            authorUsername = payload.commentData().commentAuthorUsername();
            originalBody = payload.commentData().commentBody();
        }
        
        if (parentCommentId != null) {
            // Use context-aware reply for platforms that need it (GitHub)
            String replyId = reportingService.postCommentReplyWithContext(
                project,
                Long.parseLong(payload.pullRequestId()),
                parentCommentId,
                content,
                authorUsername,
                originalBody
            );
            log.info("Posted ask reply to PR {} comment {} as reply {}", 
                payload.pullRequestId(), parentCommentId, replyId);
            jobService.info(job, "posted", "Answer posted as reply to your comment");
        } else {
            // Fallback to regular comment if no parent
            String commentId = reportingService.postComment(
                project,
                Long.parseLong(payload.pullRequestId()),
                content,
                null  // No marker for ask responses
            );
            log.info("Posted ask result to PR {} as comment {}", payload.pullRequestId(), commentId);
            jobService.info(job, "posted", "Answer posted to PR");
        }
    }
    
    /**
     * Post a comment with marker. If placeholderCommentId is provided, update that comment.
     * Otherwise, delete previous comments with the same marker and post a new one.
     */
    private void postWithMarker(VcsReportingService reportingService, Project project,
                                 WebhookPayload payload, String content, String commandType, 
                                 String placeholderCommentId, Job job) throws IOException {
        String marker = getMarkerForCommandType(commandType);
        
        // If we have a placeholder comment, update it instead of creating a new one
        if (placeholderCommentId != null) {
            reportingService.updateComment(
                project,
                Long.parseLong(payload.pullRequestId()),
                placeholderCommentId,
                content,
                marker
            );
            log.info("Updated placeholder comment {} with {} result for PR {}", 
                placeholderCommentId, commandType, payload.pullRequestId());
            jobService.info(job, "posted", "Result updated in PR comment");
            return;
        }
        
        // No placeholder - delete previous response comments with the same marker
        try {
            int deleted = reportingService.deleteCommentsByMarker(
                project, 
                Long.parseLong(payload.pullRequestId()), 
                marker
            );
            if (deleted > 0) {
                log.info("Deleted {} previous {} response(s)", deleted, commandType);
            }
        } catch (Exception e) {
            log.warn("Failed to delete previous comments: {}", e.getMessage());
        }
        
        // Post new comment - marker is added by postComment() method at the END as HTML comment
        // Do NOT prepend marker here to avoid visible marker text
        String commentId = reportingService.postComment(
            project, 
            Long.parseLong(payload.pullRequestId()), 
            content,
            marker
        );
        
        log.info("Posted {} result to PR {} as comment {}", commandType, payload.pullRequestId(), commentId);
        jobService.info(job, "posted", "Result posted to PR as comment");
    }
    
    /**
     * Post an error message to VCS as a comment.
     * If placeholderCommentId is provided, update that comment with the error.
     */
    private void postErrorToVcs(EVcsProvider provider, Project project, WebhookPayload payload, 
                                 String errorMessage, String placeholderCommentId, Job job) {
        try {
            if (payload.pullRequestId() == null) {
                return;
            }
            
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            
            // Sanitize error message for user display - hide sensitive technical details
            String sanitizedMessage = sanitizeErrorForVcs(errorMessage);
            
            // Don't prepend marker - it will be added as HTML comment by postComment/updateComment
            String content = "⚠️ **CodeCrow Command Failed**\n\n" + sanitizedMessage + 
                "\n\n---\n_Check the job logs in CodeCrow for detailed error information._";
            
            // If we have a placeholder comment, update it with the error
            if (placeholderCommentId != null) {
                reportingService.updateComment(
                    project,
                    Long.parseLong(payload.pullRequestId()),
                    placeholderCommentId,
                    content,
                    CODECROW_COMMAND_MARKER
                );
                log.info("Updated placeholder comment {} with error for PR {}", placeholderCommentId, payload.pullRequestId());
            } else {
                // No placeholder - post new error comment
                reportingService.postComment(
                    project, 
                    Long.parseLong(payload.pullRequestId()), 
                    content,
                    CODECROW_COMMAND_MARKER
                );
                log.info("Posted error to PR {}", payload.pullRequestId());
            }
            
        } catch (Exception e) {
            log.error("Failed to post error to VCS: {}", e.getMessage());
        }
    }
    
    /**
     * Post an info message to VCS as a comment (for skipped/info scenarios).
     * If placeholderCommentId is provided, update that comment with the info.
     */
    private void postInfoToVcs(EVcsProvider provider, Project project, WebhookPayload payload, 
                               String infoMessage, String placeholderCommentId, Job job) {
        try {
            if (payload.pullRequestId() == null) {
                return;
            }
            
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            
            // If we have a placeholder comment, update it with the info
            if (placeholderCommentId != null) {
                reportingService.updateComment(
                    project,
                    Long.parseLong(payload.pullRequestId()),
                    placeholderCommentId,
                    infoMessage,
                    CODECROW_COMMAND_MARKER
                );
                log.info("Updated placeholder comment {} with info message for PR {}", placeholderCommentId, payload.pullRequestId());
            } else {
                // No placeholder - post new info comment
                reportingService.postComment(
                    project, 
                    Long.parseLong(payload.pullRequestId()), 
                    infoMessage,
                    CODECROW_COMMAND_MARKER
                );
                log.info("Posted info message to PR {}", payload.pullRequestId());
            }
            
        } catch (Exception e) {
            log.error("Failed to post info to VCS: {}", e.getMessage());
        }
    }
    
    /**
     * Sanitize error messages for display on VCS platforms.
     * Removes sensitive technical details like API keys, quotas, and internal stack traces.
     */
    private String sanitizeErrorForVcs(String errorMessage) {
        if (errorMessage == null) {
            return "An unexpected error occurred during processing.";
        }
        
        String lowerMessage = errorMessage.toLowerCase();
        
        // AI provider quota/rate limit errors
        if (lowerMessage.contains("quota") || lowerMessage.contains("rate limit") || 
            lowerMessage.contains("429") || lowerMessage.contains("exceeded")) {
            return "The AI provider is currently rate-limited or quota has been exceeded. " +
                   "Please try again later or contact your administrator to check the AI connection settings.";
        }
        
        // Authentication/API key errors
        if (lowerMessage.contains("401") || lowerMessage.contains("403") || 
            lowerMessage.contains("unauthorized") || lowerMessage.contains("authentication") ||
            lowerMessage.contains("api key") || lowerMessage.contains("apikey") ||
            lowerMessage.contains("invalid_api_key")) {
            return "AI provider authentication failed. " +
                   "Please contact your administrator to verify the AI connection configuration.";
        }
        
        // Model not found/invalid
        if (lowerMessage.contains("model") && (lowerMessage.contains("not found") || 
            lowerMessage.contains("invalid") || lowerMessage.contains("does not exist"))) {
            return "The configured AI model is not available. " +
                   "Please contact your administrator to update the AI connection settings.";
        }
        
        // Token limit errors
        if (lowerMessage.contains("token") && (lowerMessage.contains("limit") || 
            lowerMessage.contains("too long") || lowerMessage.contains("maximum"))) {
            return "The PR content exceeds the AI model's token limit. " +
                   "Consider breaking down large PRs or adjusting the token limitation setting.";
        }
        
        // Network/connectivity errors
        if (lowerMessage.contains("connection") || lowerMessage.contains("timeout") ||
            lowerMessage.contains("network") || lowerMessage.contains("unreachable")) {
            return "Failed to connect to the AI provider. " +
                   "Please try again later.";
        }
        
        // Command authorization errors - check BEFORE VCS errors (contains "repository")
        if (lowerMessage.contains("not authorized to use") || 
            lowerMessage.contains("not authorized to execute")) {
            // Pass through the authorization message as-is - it's already user-friendly
            return errorMessage;
        }
        
        // VCS API errors
        if (lowerMessage.contains("vcs") || lowerMessage.contains("bitbucket") || 
            lowerMessage.contains("github") || lowerMessage.contains("repository")) {
            return "An error occurred while communicating with the VCS platform. " +
                   "Please try again or contact your administrator.";
        }
        
        // Generic AI service errors - don't expose internal details
        if (lowerMessage.contains("ai service") || lowerMessage.contains("ai failed") ||
            lowerMessage.contains("generation failed") || lowerMessage.contains("unexpected error")) {
            return "The AI service encountered an error while processing your request. " +
                   "Please try again later.";
        }
        
        // For other errors, provide a generic message but don't expose technical details
        // Only show the first 200 chars if they don't contain sensitive info
        if (errorMessage.length() > 200 || errorMessage.contains("{") || 
            errorMessage.contains("Exception") || errorMessage.contains("at org.")) {
            return "An error occurred while processing your request. " +
                   "Please check the job logs for more details.";
        }
        
        return errorMessage;
    }
    
    /**
     * Post a placeholder comment indicating CodeCrow is processing.
     * Returns the comment ID for later updating.
     */
    private String postPlaceholderComment(EVcsProvider provider, Project project, 
                                          WebhookPayload payload, Job job) {
        try {
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            
            String commandType = payload.getCodecrowCommand() != null 
                ? payload.getCodecrowCommand().type().name().toLowerCase() 
                : "default";
            
            String placeholderContent = getPlaceholderMessage(commandType);
            String marker = getMarkerForCommandType(commandType);
            
            // Delete any previous comments with the same marker before posting placeholder
            try {
                int deleted = reportingService.deleteCommentsByMarker(
                    project, 
                    Long.parseLong(payload.pullRequestId()), 
                    marker
                );
                if (deleted > 0) {
                    log.info("Deleted {} previous {} response(s) before posting placeholder", deleted, commandType);
                }
            } catch (Exception e) {
                log.warn("Failed to delete previous comments: {}", e.getMessage());
            }
            
            String commentId = reportingService.postComment(
                project,
                Long.parseLong(payload.pullRequestId()),
                placeholderContent,
                marker
            );
            
            log.info("Posted placeholder comment {} for {} command on PR {}", 
                commentId, commandType, payload.pullRequestId());
            jobService.info(job, "placeholder_posted", "Processing started - placeholder comment posted");
            
            return commentId;
            
        } catch (Exception e) {
            log.error("Failed to post placeholder comment: {}", e.getMessage(), e);
            // Don't fail the whole operation if placeholder fails
            return null;
        }
    }
    
    /**
     * Delete a placeholder comment.
     */
    private void deletePlaceholderComment(EVcsProvider provider, Project project, 
                                          WebhookPayload payload, String commentId) {
        try {
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            reportingService.deleteComment(project, Long.parseLong(payload.pullRequestId()), commentId);
            log.info("Deleted placeholder comment {}", commentId);
        } catch (Exception e) {
            log.warn("Failed to delete placeholder comment {}: {}", commentId, e.getMessage());
        }
    }
    
    /**
     * Get the placeholder message for a command type.
     */
    private String getPlaceholderMessage(String commandType) {
        return switch (commandType.toLowerCase()) {
            case "analyze" -> PLACEHOLDER_ANALYZE;
            case "summarize" -> PLACEHOLDER_SUMMARIZE;
            case "review" -> PLACEHOLDER_REVIEW;
            case "ask" -> PLACEHOLDER_ASK;
            default -> PLACEHOLDER_DEFAULT;
        };
    }
    
    /**
     * Get the comment marker for a command type.
     */
    private String getMarkerForCommandType(String commandType) {
        return switch (commandType.toLowerCase()) {
            case "summarize" -> CODECROW_SUMMARY_MARKER;
            case "review" -> CODECROW_REVIEW_MARKER;
            default -> CODECROW_COMMAND_MARKER;
        };
    }
}
