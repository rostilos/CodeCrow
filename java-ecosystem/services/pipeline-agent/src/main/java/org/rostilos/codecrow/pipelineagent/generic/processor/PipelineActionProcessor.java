package org.rostilos.codecrow.pipelineagent.generic.processor;

import jakarta.validation.Valid;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.ProjectValidationService;
import org.rostilos.codecrow.analysisengine.service.branch.BranchAnalysisGateService;
import org.rostilos.codecrow.core.model.job.Job;
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

    private final ProjectValidationService projectService;
    private final PullRequestAnalysisProcessor pullRequestAnalysisProcessor;
    private final BranchAnalysisProcessor branchAnalysisProcessor;
    private final BranchAnalysisGateService branchAnalysisGateService;

    public PipelineActionProcessor(
            ProjectValidationService projectService,
            PullRequestAnalysisProcessor pullRequestAnalysisProcessor,
            BranchAnalysisProcessor branchAnalysisProcessor,
            BranchAnalysisGateService branchAnalysisGateService
    ) {
        this.projectService = projectService;
        this.pullRequestAnalysisProcessor = pullRequestAnalysisProcessor;
        this.branchAnalysisProcessor = branchAnalysisProcessor;
        this.branchAnalysisGateService = branchAnalysisGateService;
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
        return processPipelineActionWithConsumer(request, consumer, null);
    }

    public Map<String, Object> processPipelineActionWithConsumer(
            AnalysisProcessRequest request,
            EventConsumer consumer,
            Job job
    ) throws GeneralSecurityException {

        try {
            if (request instanceof PrProcessRequest prRequest && job != null) {
                // The persisted Job identifies the accepted analysis attempt across
                // process recovery. Redis queue delivery gets its own transient UUID.
                prRequest.setAnalysisRunKey(job.getExternalId());
            }
            Project project = projectService.getProjectWithConnections(request.getProjectId());
            boolean dependenciesGated = job != null;
            if (dependenciesGated) {
                BranchAnalysisGateService.GateResult gateResult =
                        branchAnalysisGateService.awaitDependencies(
                                project.getId(), job, consumer::accept);
                if (gateResult == BranchAnalysisGateService.GateResult.SUPERSEDED) {
                    return Map.of(
                            "status", "ignored",
                            "message", "Superseded by a newer branch analysis job");
                }
            }

            if(request.getAnalysisType() == AnalysisType.BRANCH_ANALYSIS) {
                if (dependenciesGated) {
                    return branchAnalysisProcessor.processAfterDependencyGate(
                            (BranchProcessRequest) request, consumer::accept);
                }
                return branchAnalysisProcessor.process(
                        (BranchProcessRequest) request, consumer::accept);
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
