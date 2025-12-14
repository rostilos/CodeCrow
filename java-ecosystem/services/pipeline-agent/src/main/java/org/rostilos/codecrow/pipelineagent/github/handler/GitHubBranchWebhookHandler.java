package org.rostilos.codecrow.pipelineagent.github.handler;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.util.BranchPatternMatcher;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.BranchProcessRequest;
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
 * Webhook handler for GitHub push events (branch analysis).
 */
@Component
public class GitHubBranchWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(GitHubBranchWebhookHandler.class);
    
    private static final Set<String> SUPPORTED_BRANCH_EVENTS = Set.of("push");
    
    private final BranchAnalysisProcessor branchAnalysisProcessor;
    
    public GitHubBranchWebhookHandler(
            BranchAnalysisProcessor branchAnalysisProcessor
    ) {
        this.branchAnalysisProcessor = branchAnalysisProcessor;
    }
    
    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITHUB;
    }
    
    @Override
    public boolean supportsEvent(String eventType) {
        return SUPPORTED_BRANCH_EVENTS.contains(eventType);
    }
    
    @Override
    public WebhookResult handle(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer) {
        String eventType = payload.eventType();
        
        log.info("Handling GitHub push event for project {}", project.getId());
        
        // Skip branch deletions
        if (payload.commitHash() == null) {
            log.info("Ignoring branch deletion event");
            return WebhookResult.ignored("Branch deletion events are not analyzed");
        }
        
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
            
            String branchName = payload.sourceBranch();
            
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
}
