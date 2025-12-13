package org.rostilos.codecrow.pipelineagent.bitbucket.handler;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.util.BranchPatternMatcher;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.pipelineagent.generic.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhook.handler.WebhookHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Webhook handler for Bitbucket Cloud events.
 * Bridges the generic webhook processing to existing Bitbucket-specific processors.
 */
@Component
public class BitbucketCloudPullRequestWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(BitbucketCloudPullRequestWebhookHandler.class);
    
    /**
     * Bitbucket PR event types that trigger analysis.
     */
    private static final Set<String> SUPPORTED_PR_EVENTS = Set.of(
        "pullrequest:created",
        "pullrequest:updated"
    );
    
    private final PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    
    public BitbucketCloudPullRequestWebhookHandler(
            PullRequestAnalysisProcessor pullRequestAnalysisProcessor
    ) {
        this.pullRequestAnalysisProcessor = pullRequestAnalysisProcessor;
    }
    
    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.BITBUCKET_CLOUD;
    }
    
    @Override
    public boolean supportsEvent(String eventType) {
        return SUPPORTED_PR_EVENTS.contains(eventType);
    }
    
    @Override
    public WebhookResult handle(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer) {
        String eventType = payload.eventType();
        
        log.info("Handling Bitbucket Cloud PR event: {} for project {}", eventType, project.getId());
        
        try {
            String validationError = validateProjectConnections(project);
            if (validationError != null) {
                log.warn("Project {} validation failed: {}", project.getId(), validationError);
                return WebhookResult.error(validationError);
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
    
    /**
     * Validate that the project has required connections configured.
     * @return error message if validation fails, null if valid
     */
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
     * Check if a PR should be analyzed based on the project's branch configuration.
     * If no patterns are configured, all PRs are analyzed.
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
    
    private WebhookResult handlePullRequestEvent(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer) {
        try {
            // Convert WebhookPayload to PrProcessRequest
            PrProcessRequest request = new PrProcessRequest();
            request.projectId = project.getId();
            request.pullRequestId = Long.parseLong(payload.pullRequestId());
            request.sourceBranchName = payload.sourceBranch();
            request.targetBranchName = payload.targetBranch();
            request.commitHash = payload.commitHash();
            request.analysisType = AnalysisType.PR_REVIEW;
            
            log.info("Processing PR analysis: project={}, PR={}, source={}, target={}", 
                    project.getId(), request.pullRequestId, request.sourceBranchName, request.targetBranchName);
            
            // Create EventConsumer wrapper for the processor
            PullRequestAnalysisProcessor.EventConsumer processorConsumer = event -> {
                if (eventConsumer != null) {
                    eventConsumer.accept(event);
                }
            };
            
            // Delegate to existing processor
            Map<String, Object> result = pullRequestAnalysisProcessor.process(request, processorConsumer, project);
            
            boolean cached = Boolean.TRUE.equals(result.get("cached"));
            if (cached) {
                return WebhookResult.success("Analysis result retrieved from cache", result);
            }
            
            return WebhookResult.success("PR analysis completed", result);
            
        } catch (Exception e) {
            log.error("PR analysis failed for project {}", project.getId(), e);
            return WebhookResult.error("PR analysis failed: " + e.getMessage());
        }
    }
}
