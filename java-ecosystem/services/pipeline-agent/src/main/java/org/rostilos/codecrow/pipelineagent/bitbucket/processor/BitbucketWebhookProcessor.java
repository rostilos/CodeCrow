package org.rostilos.codecrow.pipelineagent.bitbucket.processor;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.pipelineagent.bitbucket.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.pipelineagent.generic.processor.WebhookProcessor;
import org.rostilos.codecrow.pipelineagent.generic.service.ProjectService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.Map;

import java.io.IOException;
import java.security.GeneralSecurityException;

/**
 * @deprecated Use {@link org.rostilos.codecrow.pipelineagent.generic.processor.WebhookProcessor} instead.
 * This Bitbucket-specific processor is deprecated in favor of the generic processor
 * that works with any VCS provider through the VcsServiceFactory.
 * 
 * Service for processing Bitbucket webhook events.
 * Orchestrates code analysis workflow including caching, AI analysis, and result posting.
 */
@Deprecated
@Service
public class BitbucketWebhookProcessor {
    private static final Logger log = LoggerFactory.getLogger(BitbucketWebhookProcessor.class);

    private final ProjectService projectService;
    private final PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    private final BranchAnalysisProcessor branchAnalysisProcessor;

    public BitbucketWebhookProcessor(
            ProjectService projectService,
            @Qualifier("bitbucketPullRequestAnalysisProcessor") PullRequestAnalysisProcessor pullRequestAnalysisProcessor,
            @Qualifier("bitbucketBranchAnalysisProcessor") BranchAnalysisProcessor branchAnalysisProcessor
    ) {
        this.projectService = projectService;
        this.pullRequestAnalysisProcessor = pullRequestAnalysisProcessor;
        this.branchAnalysisProcessor = branchAnalysisProcessor;
    }

    /**
     * @deprecated Use {@link WebhookProcessor.EventConsumer} instead.
     */
    @Deprecated
    public interface EventConsumer extends WebhookProcessor.EventConsumer {
    }

    /**
     * New: Process webhook with an EventConsumer to receive intermediate events.
     * This mirrors processWebhook but forwards events from the AI client to the provided consumer.
     *
     * @param request  webhook payload
     * @param consumer event consumer invoked for each streamed event (ndjson objects)
     * @return AI response map (final result)
     */
    public Map<String, Object> processWebhookWithConsumer(
            @Valid @RequestBody AnalysisProcessRequest request,
            WebhookProcessor.EventConsumer consumer
    ) throws GeneralSecurityException {

        try {
            Project project = projectService.getProjectWithConnections(request.getProjectId());

            switch (request.getAnalysisType()) {
                case BRANCH_ANALYSIS:
                    return branchAnalysisProcessor.process((BranchProcessRequest) request, consumer::accept);
                default:
                    return pullRequestAnalysisProcessor.process((PrProcessRequest) request, consumer, project);
            }
        } catch (IOException e) {
            log.error("IOException during webhook processing: {}", e.getMessage(), e);
            consumer.accept(Map.of(
                    "type", "error",
                    "message", "Processing failed due to I/O error: " + e.getMessage()
            ));
            return Map.of("status", "error", "message", e.getMessage());
        }
    }
}
