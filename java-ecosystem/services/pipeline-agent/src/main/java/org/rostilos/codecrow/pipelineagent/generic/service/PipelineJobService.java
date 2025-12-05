package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLogLevel;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.pipelineagent.bitbucket.processor.BitbucketWebhookProcessor;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.PrProcessRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.Optional;

/**
 * Service for creating and managing jobs during pipeline processing.
 * Provides helper methods to create jobs and log to both streaming consumers and job logs.
 */
@Service
public class PipelineJobService {

    private static final Logger log = LoggerFactory.getLogger(PipelineJobService.class);

    private final JobService jobService;
    private final ProjectRepository projectRepository;

    public PipelineJobService(JobService jobService, ProjectRepository projectRepository) {
        this.jobService = jobService;
        this.projectRepository = projectRepository;
    }

    /**
     * Create a job for a pipeline-triggered PR analysis.
     */
    public Job createPipelinePrJob(PrProcessRequest request) {
        Long projectId = request.getProjectId();
        Optional<Project> projectOpt = projectRepository.findById(projectId);
        
        if (projectOpt.isEmpty()) {
            log.warn("Cannot create job: project not found with ID {}", projectId);
            return null;
        }
        
        Project project = projectOpt.get();
        log.info("Creating pipeline job for PR analysis: project={}, PR={}", projectId, request.pullRequestId);
        
        return jobService.createPrAnalysisJob(
                project,
                request.pullRequestId,
                request.sourceBranchName,
                request.targetBranchName,
                null, // commitHash - not available in request
                JobTriggerSource.PIPELINE,
                null  // triggeredBy - pipeline has no user context
        );
    }

    /**
     * Create a job for a pipeline-triggered branch analysis.
     */
    public Job createPipelineBranchJob(BranchProcessRequest request) {
        Long projectId = request.getProjectId();
        Optional<Project> projectOpt = projectRepository.findById(projectId);
        
        if (projectOpt.isEmpty()) {
            log.warn("Cannot create job: project not found with ID {}", projectId);
            return null;
        }
        
        Project project = projectOpt.get();
        log.info("Creating pipeline job for branch analysis: project={}, branch={}", 
                projectId, request.getTargetBranchName());
        
        return jobService.createBranchAnalysisJob(
                project,
                request.getTargetBranchName(),
                null, // commitHash - not available in request
                JobTriggerSource.PIPELINE,
                null  // triggeredBy - pipeline has no user context
        );
    }

    /**
     * Create an EventConsumer that logs to both the streaming response and the job.
     */
    public BitbucketWebhookProcessor.EventConsumer createDualConsumer(
            Job job,
            BitbucketWebhookProcessor.EventConsumer streamConsumer
    ) {
        return event -> {
            // Forward to stream consumer
            if (streamConsumer != null) {
                streamConsumer.accept(event);
            }
            
            // Log to job
            if (job != null) {
                String type = (String) event.getOrDefault("type", "info");
                String message = (String) event.getOrDefault("message", "Processing...");
                String state = (String) event.getOrDefault("state", "processing");
                
                JobLogLevel level = switch (type) {
                    case "error" -> JobLogLevel.ERROR;
                    case "warning", "warn" -> JobLogLevel.WARN;
                    case "debug" -> JobLogLevel.DEBUG;
                    default -> JobLogLevel.INFO;
                };
                
                jobService.addLog(job, level, state, message, event);
                
                // Update progress if provided
                Object progressObj = event.get("progress");
                if (progressObj instanceof Number) {
                    job.setProgress(((Number) progressObj).intValue());
                }
            }
        };
    }

    /**
     * Complete or fail a job based on processing results.
     * Checks if the result indicates a failure and marks the job accordingly.
     */
    public void completeJob(Job job, Map<String, Object> result) {
        if (job == null) return;
        
        try {
            // Check for various failure indicators in the result
            boolean isFailed = false;
            String failureReason = null;
            
            // Check explicit status
            Object status = result.get("status");
            if ("error".equals(status) || "failed".equals(status)) {
                isFailed = true;
                failureReason = (String) result.get("message");
            }
            
            // Check if analysis result indicates failure (e.g., "Failed to retrieve pull request")
            Object analysisResult = result.get("result");
            if (analysisResult instanceof Map) {
                @SuppressWarnings("unchecked")
                Map<String, Object> resultMap = (Map<String, Object>) analysisResult;
                Object comment = resultMap.get("comment");
                if (comment instanceof String) {
                    String commentStr = (String) comment;
                    if (commentStr.toLowerCase().contains("failed to") || 
                        commentStr.toLowerCase().contains("unable to") ||
                        commentStr.toLowerCase().contains("cannot perform") ||
                        commentStr.toLowerCase().contains("error:")) {
                        isFailed = true;
                        failureReason = commentStr;
                    }
                }
            }
            
            // Check for warning type with failure indicators
            Object type = result.get("type");
            if ("warning".equals(type)) {
                Object message = result.get("message");
                if (message instanceof String) {
                    String msgStr = (String) message;
                    if (msgStr.toLowerCase().contains("failed")) {
                        isFailed = true;
                        if (failureReason == null) {
                            failureReason = msgStr;
                        }
                    }
                }
            }
            
            if (isFailed) {
                log.info("Job {} marked as failed: {}", job.getExternalId(), failureReason);
                jobService.failJob(job, failureReason != null ? failureReason : "Analysis failed");
            } else {
                // Check for analysis ID to log success details
                Object analysisIdObj = result.get("analysisId");
                if (analysisIdObj != null) {
                    Long analysisId = ((Number) analysisIdObj).longValue();
                    jobService.info(job, "complete", "Analysis completed. Analysis ID: " + analysisId);
                }
                jobService.completeJob(job);
            }
        } catch (Exception e) {
            log.error("Error completing job {}", job.getExternalId(), e);
            // On error processing the result, fail the job
            try {
                jobService.failJob(job, "Error processing result: " + e.getMessage());
            } catch (Exception ex) {
                log.error("Failed to mark job as failed", ex);
            }
        }
    }

    /**
     * Fail a job with an error.
     */
    public void failJob(Job job, String errorMessage) {
        if (job == null) return;
        
        try {
            jobService.failJob(job, errorMessage);
        } catch (Exception e) {
            log.error("Error failing job {}", job != null ? job.getExternalId() : "null", e);
        }
    }

    public JobService getJobService() {
        return jobService;
    }
}
