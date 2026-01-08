package org.rostilos.codecrow.pipelineagent.webhookhandler.gitlab;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
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
 * Webhook handler for GitLab Merge Request merge events.
 * Triggers branch reconciliation when an MR is merged to update issue status.
 * 
 * This handler specifically listens for 'merge_request' events with action='merge',
 * and delegates to the BranchAnalysisProcessor for issue reconciliation.
 */
@Component
public class GitLabMrMergeWebhookHandler extends AbstractWebhookHandler implements WebhookHandler {
    
    private static final Logger log = LoggerFactory.getLogger(GitLabMrMergeWebhookHandler.class);
    
    private static final Set<String> SUPPORTED_EVENTS = Set.of("merge_request");
    
    private final BranchAnalysisProcessor branchAnalysisProcessor;
    
    public GitLabMrMergeWebhookHandler(BranchAnalysisProcessor branchAnalysisProcessor) {
        this.branchAnalysisProcessor = branchAnalysisProcessor;
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
        // Check if this is an MR merge event
        // GitLab sends object_attributes.action = "merge" when MR is merged
        String action = payload.rawPayload().path("object_attributes").path("action").asText(null);
        String state = payload.rawPayload().path("object_attributes").path("state").asText(null);
        
        // Accept either action=merge or state=merged
        boolean isMerged = "merge".equals(action) || "merged".equals(state);
        
        if (!isMerged) {
            // Not a merge event, let other handlers process
            return WebhookResult.ignored("Not an MR merge event");
        }
        
        log.info("Handling GitLab MR merge event for project {}", project.getId());
        
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
            
            return handleMrMergeEvent(payload, project, eventConsumer);
        } catch (Exception e) {
            log.error("Error processing MR merge event for project {}", project.getId(), e);
            return WebhookResult.error("Processing failed: " + e.getMessage());
        }
    }
    
    private WebhookResult handleMrMergeEvent(
            WebhookPayload payload,
            Project project,
            Consumer<Map<String, Object>> eventConsumer
    ) {
        try {
            String targetBranch = payload.targetBranch();
            
            // Get merge commit SHA from GitLab payload
            String mergeCommitSha = payload.rawPayload().path("object_attributes").path("merge_commit_sha").asText(null);
            
            // Fallback to last commit if merge_commit_sha not available
            String commitHash = mergeCommitSha != null ? mergeCommitSha : payload.commitHash();
            
            // Get MR number (iid in GitLab)
            Long mrNumber = null;
            if (payload.pullRequestId() != null) {
                mrNumber = Long.parseLong(payload.pullRequestId());
            } else {
                // Try to get from object_attributes.iid
                int iid = payload.rawPayload().path("object_attributes").path("iid").asInt(0);
                if (iid > 0) {
                    mrNumber = (long) iid;
                }
            }
            
            if (commitHash == null) {
                log.warn("No commit hash available for MR merge event");
                return WebhookResult.error("No commit hash available");
            }
            
            BranchProcessRequest request = new BranchProcessRequest();
            request.projectId = project.getId();
            request.targetBranchName = targetBranch;
            request.commitHash = commitHash;
            request.analysisType = AnalysisType.BRANCH_ANALYSIS;
            request.sourcePrNumber = mrNumber; // Pass MR number for resolution tracking
            
            log.info("Processing branch reconciliation after MR merge: project={}, branch={}, commit={}, MR={}", 
                    project.getId(), targetBranch, commitHash, mrNumber);
            
            Consumer<Map<String, Object>> processorConsumer = event -> {
                if (eventConsumer != null) {
                    eventConsumer.accept(event);
                }
            };
            
            Map<String, Object> result = branchAnalysisProcessor.process(request, processorConsumer);
            
            return WebhookResult.success("Branch reconciliation completed after MR !" + mrNumber + " merge", result);
            
        } catch (Exception e) {
            log.error("Branch reconciliation failed for project {}", project.getId(), e);
            return WebhookResult.error("Branch reconciliation failed: " + e.getMessage());
        }
    }
}
