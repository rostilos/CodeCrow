package org.rostilos.codecrow.pipelineagent.bitbucket.processor;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.pipelineagent.AnalysisRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.pipelineagent.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.dto.request.pipelineagent.PrProcessRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.pipelineagent.bitbucket.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.pipelineagent.generic.service.ProjectService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.Map;

import java.io.IOException;
import java.security.GeneralSecurityException;

/**
 * Service for processing Bitbucket webhook events.
 * Orchestrates code analysis workflow including caching, AI analysis, and result posting.
 */
@Service
public class BitbucketWebhookProcessor {
    private static final Logger log = LoggerFactory.getLogger(BitbucketWebhookProcessor.class);

    private final ProjectService projectService;
    private final PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    private final BranchAnalysisProcessor branchAnalysisProcessor;

    public BitbucketWebhookProcessor(
            ProjectService projectService,
            PullRequestAnalysisProcessor pullRequestAnalysisProcessor,
            BranchAnalysisProcessor branchAnalysisProcessor
    ) {
        this.projectService = projectService;
        this.pullRequestAnalysisProcessor = pullRequestAnalysisProcessor;
        this.branchAnalysisProcessor = branchAnalysisProcessor;
    }

    public interface EventConsumer {
        /**
         * Accept an event map produced by the analysis worker (parsed NDJSON object).
         *
         * @param event event payload as map
         */
        void accept(java.util.Map<String, Object> event);
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
            @Valid @RequestBody AnalysisRequest request,
            EventConsumer consumer
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
