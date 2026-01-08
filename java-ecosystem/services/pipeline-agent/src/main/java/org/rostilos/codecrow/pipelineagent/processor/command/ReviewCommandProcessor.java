package org.rostilos.codecrow.pipelineagent.processor.command;

import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient.ReviewRequest;
import org.rostilos.codecrow.analysisengine.aiclient.AiCommandClient.ReviewResult;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.pipelineagent.webhookhandler.CommentCommandWebhookHandler.CommentCommandProcessor;
import org.rostilos.codecrow.pipelineagent.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.webhookhandler.WebhookHandler.WebhookResult;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor;
import org.rostilos.codecrow.vcsclient.utils.VcsConnectionCredentialsExtractor.VcsConnectionCredentials;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.security.GeneralSecurityException;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Processor for /codecrow review command.
 * <p>
 * Generates a concise code review for the PR without diagrams.
 * Focuses on:
 * - Code quality issues
 * - Security concerns
 * - Best practice violations
 * - Suggestions for improvement
 */
@Component("reviewCommandProcessor")
public class ReviewCommandProcessor implements CommentCommandProcessor {
    
    private static final Logger log = LoggerFactory.getLogger(ReviewCommandProcessor.class);
    
    /** Maximum review length to avoid hitting VCS comment limits */
    private static final int MAX_REVIEW_LENGTH = 65000;
    
    private final AiCommandClient aiCommandClient;
    private final TokenEncryptionService tokenEncryptionService;
    private final VcsConnectionCredentialsExtractor credentialsExtractor;
    
    public ReviewCommandProcessor(
            AiCommandClient aiCommandClient,
            TokenEncryptionService tokenEncryptionService
    ) {
        this.aiCommandClient = aiCommandClient;
        this.tokenEncryptionService = tokenEncryptionService;
        this.credentialsExtractor = new VcsConnectionCredentialsExtractor(tokenEncryptionService);
    }
    
    @Override
    public WebhookResult process(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        log.info("Processing review command for project={}, PR={}", 
            project.getId(), payload.pullRequestId());
        
        try {
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "fetching_context",
                "message", "Fetching PR context..."
            ));
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "generating_review",
                "message", "Generating code review..."
            ));
            
            // Generate review with AI
            String review = generateReview(project, payload);
            
            if (review == null || review.isBlank()) {
                log.warn("Review generation returned empty result");
                return WebhookResult.error("Failed to generate code review - empty result");
            }
            
            // Truncate if needed
            if (review.length() > MAX_REVIEW_LENGTH) {
                review = review.substring(0, MAX_REVIEW_LENGTH) + "\n\n... (truncated)";
            }
            
            eventConsumer.accept(Map.of(
                "type", "status",
                "state", "complete",
                "message", "Code review generated successfully"
            ));
            
            // Return success with content for posting
            return WebhookResult.success("Code review generated successfully", Map.of(
                "content", review,
                "commandType", "review"
            ));
            
        } catch (Exception e) {
            log.error("Error generating review: {}", e.getMessage(), e);
            return WebhookResult.error("Failed to generate code review: " + e.getMessage());
        }
    }
    
    /**
     * Generate a concise code review using the AI service.
     */
    private String generateReview(Project project, WebhookPayload payload) {
        try {
            ReviewRequest request = buildReviewRequest(project, payload);
            
            if (request == null) {
                log.warn("Failed to build review request - missing AI or VCS configuration");
                return "⚠️ **Review Failed**\n\nCould not generate review - missing AI or VCS configuration.";
            }
            
            log.info("Calling AI service to generate code review...");
            
            ReviewResult result = aiCommandClient.review(request, event -> {
                log.debug("AI review event: {}", event);
            });
            
            log.info("Code review generated successfully");
            return result.review();
            
        } catch (IOException e) {
            log.error("Failed to generate review via AI: {}", e.getMessage(), e);
            return "⚠️ **Review Failed**\n\nError generating code review: " + e.getMessage();
        } catch (Exception e) {
            log.error("Unexpected error generating review: {}", e.getMessage(), e);
            return "⚠️ **Review Failed**\n\nUnexpected error: " + e.getMessage();
        }
    }
    
    /**
     * Build the request for the AI review endpoint.
     */
    private ReviewRequest buildReviewRequest(Project project, WebhookPayload payload) {
        try {
            // Get VCS info
            VcsInfo vcsInfo = getVcsInfo(project);
            if (vcsInfo == null) {
                log.error("No VCS connection configured for project");
                return null;
            }
            
            // Get AI connection
            if (project.getAiBinding() == null || project.getAiBinding().getAiConnection() == null) {
                log.error("No AI connection configured for project");
                return null;
            }
            
            AIConnection aiConnection = project.getAiBinding().getAiConnection();
            String decryptedApiKey = tokenEncryptionService.decrypt(aiConnection.getApiKeyEncrypted());
            
            // Get VCS credentials using centralized extractor
            VcsConnection vcsConnection = vcsInfo.vcsConnection();
            VcsConnectionCredentials credentials = credentialsExtractor.extractCredentials(vcsConnection);
            
            Long prId = payload.pullRequestId() != null 
                ? Long.parseLong(payload.pullRequestId()) 
                : null;
            
            return new ReviewRequest(
                project.getId(),
                vcsInfo.workspace(),
                vcsInfo.repoSlug(),
                project.getWorkspace() != null ? project.getWorkspace().getName() : "",
                project.getNamespace() != null ? project.getNamespace() : "",
                aiConnection.getProviderKey().name(),
                aiConnection.getAiModel(),
                decryptedApiKey,
                prId,
                payload.sourceBranch(),
                payload.targetBranch(),
                payload.commitHash(),
                credentials.oAuthClient(),
                credentials.oAuthSecret(),
                credentials.accessToken(),
                aiConnection.getTokenLimitation(),
                credentials.vcsProviderString()
            );
            
        } catch (GeneralSecurityException e) {
            log.error("Failed to decrypt credentials: {}", e.getMessage());
            return null;
        }
    }
    
    /**
     * Helper record to hold VCS connection info.
     */
    private record VcsInfo(VcsConnection vcsConnection, String workspace, String repoSlug) {}
    
    /**
     * Get VCS connection info from project using unified accessor.
     */
    private VcsInfo getVcsInfo(Project project) {
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null && vcsInfo.getVcsConnection() != null) {
            return new VcsInfo(vcsInfo.getVcsConnection(), vcsInfo.getRepoWorkspace(), vcsInfo.getRepoSlug());
        }
        return null;
    }
}
