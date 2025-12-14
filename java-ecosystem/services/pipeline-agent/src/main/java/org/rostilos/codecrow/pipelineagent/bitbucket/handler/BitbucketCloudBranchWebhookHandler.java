package org.rostilos.codecrow.pipelineagent.bitbucket.handler;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.util.BranchPatternMatcher;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.processor.analysis.BranchAnalysisProcessor;
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
 * Webhook handler for Bitbucket Cloud branch/push events.
 * Bridges the generic webhook processing to existing Bitbucket-specific processors.
 */
@Component
public class BitbucketCloudBranchWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(BitbucketCloudBranchWebhookHandler.class);
    
    /**
     * Bitbucket events that trigger branch analysis.
     * These are typically PR merge events or direct pushes to branches.
     */
    private static final Set<String> SUPPORTED_BRANCH_EVENTS = Set.of(
        "pullrequest:fulfilled",
        "repo:push"
    );
    
    private final BranchAnalysisProcessor branchAnalysisProcessor;
    
    public BitbucketCloudBranchWebhookHandler(
            BranchAnalysisProcessor branchAnalysisProcessor
    ) {
        this.branchAnalysisProcessor = branchAnalysisProcessor;
    }
    
    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.BITBUCKET_CLOUD;
    }
    
    @Override
    public boolean supportsEvent(String eventType) {
        return SUPPORTED_BRANCH_EVENTS.contains(eventType);
    }
    
    @Override
    public WebhookResult handle(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer) {
        String eventType = payload.eventType();
        
        log.info("Handling Bitbucket Cloud branch event: {} for project {}", eventType, project.getId());
        
        try {
            String validationError = validateProjectConnections(project);
            if (validationError != null) {
                log.warn("Project {} validation failed: {}", project.getId(), validationError);
                return WebhookResult.error(validationError);
            }
            
            if (!project.isBranchAnalysisEnabled()) {
                log.info("Branch analysis is disabled for project {}", project.getId());
                return WebhookResult.ignored("Branch analysis is disabled for this project");
            }
            
            String branchName = determineBranchName(payload);
            
            if (branchName == null) {
                log.warn("Could not determine branch name from payload");
                return WebhookResult.ignored("Could not determine branch name");
            }
            
            if (!shouldAnalyzeBranch(project, branchName)) {
                log.info("Skipping branch analysis: branch '{}' does not match configured patterns for project {}", 
                        branchName, project.getId());
                return WebhookResult.ignored("Branch '" + branchName + "' does not match configured analysis patterns");
            }
            
            BranchProcessRequest request = new BranchProcessRequest();
            request.projectId = project.getId();
            request.targetBranchName = branchName;
            request.commitHash = payload.commitHash();
            request.analysisType = AnalysisType.BRANCH_ANALYSIS;
            
            log.info("Processing branch analysis: project={}, branch={}, commit={}", 
                    project.getId(), branchName, payload.commitHash());
            
            Consumer<Map<String, Object>> processorConsumer = event -> {
                if (eventConsumer != null) {
                    eventConsumer.accept(event);
                }
            };
            
            Map<String, Object> result = branchAnalysisProcessor.process(request, processorConsumer);
            
            return WebhookResult.success("Branch analysis completed", result);
            
        } catch (Exception e) {
            log.error("Branch analysis failed for project {}", project.getId(), e);
            return WebhookResult.error("Branch analysis failed: " + e.getMessage());
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
     * Check if a branch matches the configured analysis patterns.
     * Note: isBranchAnalysisEnabled() check is done in the handle() method before this is called.
     */
    private boolean shouldAnalyzeBranch(Project project, String branchName) {
        // Check branch pattern configuration
        if (project.getConfiguration() == null) {
            return true;
        }
        
        ProjectConfig.BranchAnalysisConfig branchConfig = project.getConfiguration().branchAnalysis();
        if (branchConfig == null) {
            return true;
        }
        
        List<String> branchPushPatterns = branchConfig.branchPushPatterns();
        return BranchPatternMatcher.shouldAnalyze(branchName, branchPushPatterns);
    }
    
    /**
     * Determine the branch name to analyze based on the event type.
     * For PR merges (pullrequest:fulfilled), use the target branch.
     * For direct pushes (repo:push), use the source branch.
     */
    private String determineBranchName(WebhookPayload payload) {
        String eventType = payload.eventType();
        
        if ("pullrequest:fulfilled".equals(eventType)) {
            return payload.targetBranch();
        } else if ("repo:push".equals(eventType)) {
            return payload.sourceBranch();
        }
        
        if (payload.targetBranch() != null) {
            return payload.targetBranch();
        }
        return payload.sourceBranch();
    }
}
