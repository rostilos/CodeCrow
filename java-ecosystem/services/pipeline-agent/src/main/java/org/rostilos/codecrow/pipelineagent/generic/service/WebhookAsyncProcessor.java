package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhook.handler.WebhookHandler;
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
     * Process a webhook asynchronously with proper transactional context.
     */
    @Async
    @Transactional
    public void processWebhookAsync(
            EVcsProvider provider,
            Long projectId,
            WebhookPayload payload,
            WebhookHandler handler,
            Job job
    ) {
        try {
            // Re-fetch project within transaction to ensure all lazy associations are available
            Project project = projectRepository.findById(projectId)
                .orElseThrow(() -> new IllegalStateException("Project not found: " + projectId));
            
            // Initialize lazy associations we'll need
            initializeProjectAssociations(project);
            
            jobService.startJob(job);
            
            // Create event consumer that logs to job
            WebhookHandler.WebhookResult result = handler.handle(payload, project, event -> {
                String message = (String) event.getOrDefault("message", "Processing...");
                String state = (String) event.getOrDefault("state", "processing");
                jobService.info(job, state, message);
            });
            
            // Check if the webhook was ignored (e.g., not a CodeCrow command)
            if ("ignored".equals(result.status())) {
                log.info("Webhook ignored: {}", result.message());
                jobService.info(job, "ignored", result.message());
                jobService.completeJob(job);
                return;
            }
            
            if (result.success()) {
                // Post result to VCS if there's content to post
                postResultToVcs(provider, project, payload, result, job);
                
                if (result.data().containsKey("analysisId")) {
                    Long analysisId = ((Number) result.data().get("analysisId")).longValue();
                    jobService.info(job, "complete", "Analysis completed. Analysis ID: " + analysisId);
                }
                jobService.completeJob(job);
            } else {
                // Post error to VCS
                postErrorToVcs(provider, project, payload, result.message(), job);
                jobService.failJob(job, result.message());
            }
            
        } catch (Exception e) {
            log.error("Error processing webhook for job {}", job.getExternalId(), e);
            try {
                Project project = projectRepository.findById(projectId).orElse(null);
                if (project != null) {
                    initializeProjectAssociations(project);
                    postErrorToVcs(provider, project, payload, "Processing failed: " + e.getMessage(), job);
                }
            } catch (Exception postError) {
                log.error("Failed to post error to VCS: {}", postError.getMessage());
            }
            jobService.failJob(job, "Processing failed: " + e.getMessage());
        }
    }
    
    /**
     * Initialize lazy associations that will be needed for VCS operations.
     */
    private void initializeProjectAssociations(Project project) {
        // Force initialization of VCS connections
        if (project.getVcsBinding() != null) {
            var vcsConn = project.getVcsBinding().getVcsConnection();
            if (vcsConn != null) {
                // Touch to initialize
                vcsConn.getConnectionType();
                vcsConn.getProviderType();
            }
        }
        if (project.getVcsRepoBinding() != null) {
            var vcsConn = project.getVcsRepoBinding().getVcsConnection();
            if (vcsConn != null) {
                vcsConn.getConnectionType();
                vcsConn.getProviderType();
            }
        }
    }
    
    /**
     * Post the result of a command to VCS as a comment.
     */
    private void postResultToVcs(EVcsProvider provider, Project project, WebhookPayload payload, 
                                  WebhookHandler.WebhookResult result, Job job) {
        try {
            // Only post for command results that have content
            String commandType = (String) result.data().get("commandType");
            log.info("postResultToVcs: commandType={}, resultData={}", commandType, result.data().keySet());
            
            if (commandType == null) {
                log.debug("No commandType in result, skipping VCS post");
                return;
            }
            
            String content = (String) result.data().get("content");
            if (content == null || content.isBlank()) {
                log.debug("No content in result, skipping VCS post");
                return;
            }
            
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            
            if (payload.pullRequestId() != null) {
                // Handle "ask" differently - reply to the comment, don't delete anything
                if ("ask".equalsIgnoreCase(commandType)) {
                    log.info("Posting ASK response as reply (no deletion)");
                    postAskReply(reportingService, project, payload, content, job);
                } else {
                    // For other commands (summarize, review) - post with marker and delete previous
                    log.info("Posting {} response with marker (will delete previous)", commandType);
                    postWithMarker(reportingService, project, payload, content, commandType, job);
                }
            }
            
        } catch (Exception e) {
            log.error("Failed to post result to VCS: {}", e.getMessage(), e);
            jobService.warn(job, "post_failed", "Failed to post result to VCS: " + e.getMessage());
        }
    }
    
    /**
     * Post an "ask" response as a reply to the triggering comment.
     */
    private void postAskReply(VcsReportingService reportingService, Project project, 
                               WebhookPayload payload, String content, Job job) throws IOException {
        String parentCommentId = null;
        
        // Get the parent comment ID from the payload
        if (payload.commentData() != null) {
            parentCommentId = payload.commentData().commentId();
        }
        
        if (parentCommentId != null) {
            // Reply to the original comment
            String replyId = reportingService.postCommentReply(
                project,
                Long.parseLong(payload.pullRequestId()),
                parentCommentId,
                content
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
     * Post a comment with marker, deleting previous comments with the same marker.
     */
    private void postWithMarker(VcsReportingService reportingService, Project project,
                                 WebhookPayload payload, String content, String commandType, Job job) throws IOException {
        String marker = getMarkerForCommandType(commandType);
        
        // Delete previous response comments with the same marker
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
        
        // Post new comment with marker
        String commentContent = marker + "\n\n" + content;
        String commentId = reportingService.postComment(
            project, 
            Long.parseLong(payload.pullRequestId()), 
            commentContent,
            marker
        );
        
        log.info("Posted {} result to PR {} as comment {}", commandType, payload.pullRequestId(), commentId);
        jobService.info(job, "posted", "Result posted to PR as comment");
    }
    
    /**
     * Post an error message to VCS as a comment.
     */
    private void postErrorToVcs(EVcsProvider provider, Project project, WebhookPayload payload, 
                                 String errorMessage, Job job) {
        try {
            if (payload.pullRequestId() == null) {
                return;
            }
            
            VcsReportingService reportingService = vcsServiceFactory.getReportingService(provider);
            
            String content = CODECROW_COMMAND_MARKER + "\n\n" +
                "⚠️ **CodeCrow Command Failed**\n\n" +
                errorMessage;
            
            reportingService.postComment(
                project, 
                Long.parseLong(payload.pullRequestId()), 
                content,
                CODECROW_COMMAND_MARKER
            );
            
            log.info("Posted error to PR {}", payload.pullRequestId());
            
        } catch (Exception e) {
            log.error("Failed to post error to VCS: {}", e.getMessage());
        }
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
