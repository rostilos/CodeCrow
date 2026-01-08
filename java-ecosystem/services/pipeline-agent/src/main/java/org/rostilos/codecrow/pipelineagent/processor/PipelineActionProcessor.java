package org.rostilos.codecrow.pipelineagent.processor;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.ProjectService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.bind.annotation.RequestBody;

import java.util.Map;

import java.io.IOException;
import java.security.GeneralSecurityException;

/**
 * Generic service for processing webhook events from any VCS provider.
 * Orchestrates code analysis workflow including caching, AI analysis, and result posting.
 */
@Service
public class PipelineActionProcessor {
    private static final Logger log = LoggerFactory.getLogger(PipelineActionProcessor.class);

    private final ProjectService projectService;
    private final PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    private final BranchAnalysisProcessor branchAnalysisProcessor;

    public PipelineActionProcessor(
            ProjectService projectService,
            PullRequestAnalysisProcessor pullRequestAnalysisProcessor,
            BranchAnalysisProcessor branchAnalysisProcessor
    ) {
        this.projectService = projectService;
        this.pullRequestAnalysisProcessor = pullRequestAnalysisProcessor;
        this.branchAnalysisProcessor = branchAnalysisProcessor;
    }

    public interface EventConsumer {
        void accept(Map<String, Object> event);
    }

    /**
     * Process webhook with an EventConsumer to receive intermediate events.
     *
     * @param request  webhook payload
     * @param consumer event consumer invoked for each streamed event (ndjson objects)
     * @return AI response map (final result)
     */
    public Map<String, Object> processPipelineActionWithConsumer(
            @Valid @RequestBody AnalysisProcessRequest request,
            EventConsumer consumer
    ) throws GeneralSecurityException {

        try {
            Project project = projectService.getProjectWithConnections(request.getProjectId());

            if(request.getAnalysisType() == AnalysisType.BRANCH_ANALYSIS) {
                return branchAnalysisProcessor.process((BranchProcessRequest) request, consumer::accept);
            } else {
                return pullRequestAnalysisProcessor.process(
                        (PrProcessRequest) request,
                        consumer::accept,
                        project
                );
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
