package org.rostilos.codecrow.pipelineagent.github.webhookhandler;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.AbstractWebhookHandler;
import org.rostilos.codecrow.pipelineagent.generic.webhookhandler.WebhookHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;

/**
 * Webhook handler for GitHub Pull Request merge events.
 * Triggers branch reconciliation when a PR is merged to update issue status.
 * 
 * This handler specifically listens for 'pull_request' events with action='closed' and merged=true,
 * and delegates to the BranchAnalysisProcessor for issue reconciliation.
 */
@Component
public class GitHubPrMergeWebhookHandler extends AbstractWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(GitHubPrMergeWebhookHandler.class);
    
    private static final Set<String> SUPPORTED_EVENTS = Set.of("pull_request");
    
    private final BranchAnalysisProcessor branchAnalysisProcessor;
    
    public GitHubPrMergeWebhookHandler(
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
        return SUPPORTED_EVENTS.contains(eventType);
    }
    
    @Override
    public WebhookResult handle(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer) {
        String eventType = payload.eventType();
        
        // Check if this is a PR merge event (action=closed and merged=true)
        String action = payload.rawPayload().has("action") 
                ? payload.rawPayload().get("action").asText() 
                : null;
        
        boolean isMerged = payload.rawPayload().path("pull_request").path("merged").asBoolean(false);
        
        if (!"closed".equals(action) || !isMerged) {
            // Not a merge event, let other handlers process or ignore
            return WebhookResult.ignored("Not a PR merge event");
        }
        
        log.info("Handling GitHub PR merge event: {} for project {}", eventType, project.getId());
        
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
            
            String targetBranch = payload.targetBranch();
            if (!shouldAnalyze(project, targetBranch, AnalysisType.BRANCH_ANALYSIS)) {
                log.info("Skipping branch reconciliation: target branch '{}' does not match configured patterns for project {}", 
                        targetBranch, project.getId());
                return WebhookResult.ignored("Target branch '" + targetBranch + "' does not match configured analysis patterns");
            }
            
            return handlePrMergeEvent(payload, project, eventConsumer);
        } catch (Exception e) {
            log.error("Error processing PR merge event for project {}", project.getId(), e);
            return WebhookResult.error("Processing failed: " + e.getMessage());
        }
    }
    
    private WebhookResult handlePrMergeEvent(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        try {
            String targetBranch = payload.targetBranch();
            String mergeCommitSha = payload.rawPayload().path("pull_request").path("merge_commit_sha").asText(null);
            Long prNumber = Long.parseLong(payload.pullRequestId());
            
            // Fallback to commit hash if merge_commit_sha not available
            String commitHash = mergeCommitSha != null ? mergeCommitSha : payload.commitHash();
            
            if (commitHash == null) {
                log.warn("No commit hash available for PR merge event");
                return WebhookResult.error("No commit hash available");
            }
            
            BranchProcessRequest request = new BranchProcessRequest();
            request.projectId = project.getId();
            request.targetBranchName = targetBranch;
            request.commitHash = commitHash;
            request.analysisType = AnalysisType.BRANCH_ANALYSIS;
            request.sourcePrNumber = prNumber; // This is the key - pass PR number for resolution tracking
            
            log.info("Processing branch reconciliation after PR merge: project={}, branch={}, commit={}, PR={}", 
                    project.getId(), targetBranch, commitHash, prNumber);
            
            Consumer<Map<String, Object>> processorConsumer = event -> {
                if (eventConsumer != null) {
                    eventConsumer.accept(event);
                }
            };
            
            Map<String, Object> result = branchAnalysisProcessor.process(request, processorConsumer);
            
            return WebhookResult.success("Branch reconciliation completed after PR #" + prNumber + " merge", result);
            
        } catch (Exception e) {
            log.error("Branch reconciliation failed for project {}", project.getId(), e);
            return WebhookResult.error("Branch reconciliation failed: " + e.getMessage());
        }
    }
}
