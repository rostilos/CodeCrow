package org.rostilos.codecrow.pipelineagent.gitlab.webhookhandler;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.AbstractWebhookHandler;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Webhook handler for GitLab Push events (branch analysis).
 */
@Component
public class GitLabBranchWebhookHandler extends AbstractWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(GitLabBranchWebhookHandler.class);
    
    private static final Set<String> SUPPORTED_EVENTS = Set.of("push");
    
    private final BranchAnalysisProcessor branchAnalysisProcessor;
    private final RagOperationsService ragOperationsService;
    
    public GitLabBranchWebhookHandler(
            BranchAnalysisProcessor branchAnalysisProcessor,
            @Autowired(required = false) RagOperationsService ragOperationsService
    ) {
        this.branchAnalysisProcessor = branchAnalysisProcessor;
        this.ragOperationsService = ragOperationsService;
    }
    
    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITLAB;
    }
    
    @Override
    public boolean supportsEvent(String eventType) {
        return SUPPORTED_EVENTS.contains(eventType);
    }
    
    @Override
    public WebhookResult handle(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer) {
        log.info("Handling GitLab push event for project {} on branch {}", project.getId(), payload.sourceBranch());
        
        // Handle branch deletion (no commit hash means branch was deleted)
        if (payload.commitHash() == null) {
            return handleBranchDeletion(payload, project, eventConsumer);
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
            if (!shouldAnalyze(project, branchName, AnalysisType.BRANCH_ANALYSIS)) {
                log.info("Skipping branch analysis: branch '{}' does not match configured patterns for project {}", 
                        branchName, project.getId());
                return WebhookResult.ignored("Branch '" + branchName + "' does not match configured analysis patterns");
            }
            
            return handlePushEvent(payload, project, eventConsumer);
        } catch (Exception e) {
            log.error("Error processing push event for project {}", project.getId(), e);
            return WebhookResult.error("Processing failed: " + e.getMessage());
        }
    }

    private WebhookResult handlePushEvent(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        try {
            String branchName = payload.sourceBranch();
            
            // Create branch analysis request
            BranchProcessRequest request = new BranchProcessRequest();
            request.projectId = project.getId();
            request.targetBranchName = branchName;
            request.commitHash = payload.commitHash();
            request.analysisType = AnalysisType.BRANCH_ANALYSIS;
            
            log.info("Processing branch analysis: project={}, branch={}, commit={}", 
                    project.getId(), branchName, request.commitHash);
            
            Consumer<Map<String, Object>> processorConsumer = event -> {
                if (eventConsumer != null) {
                    eventConsumer.accept(event);
                }
            };
            
            // Delegate to branch analysis processor
            Map<String, Object> result = branchAnalysisProcessor.process(request, processorConsumer);
            
            // Check if analysis failed
            if ("error".equals(result.get("status"))) {
                String errorMessage = (String) result.getOrDefault("message", "Analysis failed");
                return WebhookResult.error("Branch analysis failed: " + errorMessage);
            }
            
            boolean cached = Boolean.TRUE.equals(result.get("cached"));
            if (cached) {
                return WebhookResult.success("Analysis result retrieved from cache", result);
            }
            
            return WebhookResult.success("Branch analysis completed", result);
            
        } catch (Exception e) {
            log.error("Branch analysis failed for project {}", project.getId(), e);
            return WebhookResult.error("Branch analysis failed: " + e.getMessage());
        }
    }
    
    /**
     * Handle branch deletion event by cleaning up RAG index.
     */
    private WebhookResult handleBranchDeletion(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        String branchName = payload.sourceBranch();
        log.info("Handling branch deletion for project={}, branch={}", project.getId(), branchName);
        
        if (ragOperationsService == null) {
            log.debug("RAG operations service not available - skipping RAG cleanup");
            return WebhookResult.ignored("Branch deleted, RAG cleanup skipped (RAG not available)");
        }
        
        try {
            boolean deleted = ragOperationsService.deleteBranchIndex(project, branchName, event -> {
                if (eventConsumer != null) {
                    eventConsumer.accept(event);
                }
            });
            
            if (deleted) {
                return WebhookResult.success("Branch deleted, RAG index cleaned up", Map.of(
                    "branch", branchName,
                    "rag_cleaned", true
                ));
            } else {
                return WebhookResult.ignored("Branch deleted, no RAG cleanup needed");
            }
        } catch (Exception e) {
            log.error("Error cleaning up RAG index for deleted branch: project={}, branch={}", 
                    project.getId(), branchName, e);
            return WebhookResult.ignored("Branch deleted, RAG cleanup failed: " + e.getMessage());
        }
    }
}
