package org.rostilos.codecrow.pipelineagent.github.webhookhandler;

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
 * Webhook handler for GitHub push events (branch analysis).
 */
@Component
public class GitHubBranchWebhookHandler extends AbstractWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(GitHubBranchWebhookHandler.class);
    
    private static final Set<String> SUPPORTED_BRANCH_EVENTS = Set.of("push");
    
    private final BranchAnalysisProcessor branchAnalysisProcessor;
    private final RagOperationsService ragOperationsService;
    
    public GitHubBranchWebhookHandler(
            BranchAnalysisProcessor branchAnalysisProcessor,
            @Autowired(required = false) RagOperationsService ragOperationsService
    ) {
        this.branchAnalysisProcessor = branchAnalysisProcessor;
        this.ragOperationsService = ragOperationsService;
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
            
            if (branchName == null) {
                log.warn("Could not determine branch name from payload");
                return WebhookResult.ignored("Could not determine branch name");
            }
            
            if (!shouldAnalyze(project, branchName, AnalysisType.BRANCH_ANALYSIS)) {
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
