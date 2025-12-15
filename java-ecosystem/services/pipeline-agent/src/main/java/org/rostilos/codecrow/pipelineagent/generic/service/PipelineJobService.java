package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLogLevel;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.analysisengine.processor.WebhookProcessor;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.Optional;

/**
 * Service for creating and managing jobs during pipeline processing.
 * Provides helper methods to create jobs and log to both streaming consumers and job logs.
 * Implements AnalysisJobService to allow analysis-engine components to use job management.
 */
@Service
public class PipelineJobService implements AnalysisJobService {

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

    public Job createRagInitialIndexJob(Project project, User triggeredBy) {
        log.info("Creating RAG initial indexing job for project: {}", project.getName());
        return jobService.createRagIndexJob(
                project,
                true,
                JobTriggerSource.API,
                triggeredBy
        );
    }

    /**
     * Implementation of AnalysisJobService interface.
     */
    @Override
    public Job createRagIndexJob(Project project, User triggeredBy) {
        return createRagInitialIndexJob(project, triggeredBy);
    }

    /**
     * Create a job for RAG indexing with configurable trigger source.
     * @param project The project to index
     * @param isInitial true for initial indexing, false for incremental update
     * @param triggerSource The source that triggered the job (WEBHOOK, API, etc.)
     * @return The created job
     */
    @Override
    public Job createRagIndexJob(Project project, boolean isInitial, JobTriggerSource triggerSource) {
        log.info("Creating RAG {} job for project: {} (trigger: {})", 
                isInitial ? "initial indexing" : "incremental update", 
                project.getName(), 
                triggerSource);
        return jobService.createRagIndexJob(
                project,
                isInitial,
                triggerSource,
                null
        );
    }

    /**
     * Alias for createRagIndexJob for backward compatibility.
     * Used by pipeline processors for RAG jobs triggered by webhooks.
     */
    public Job createPipelineRagJob(Project project, boolean isInitial, JobTriggerSource triggerSource) {
        return createRagIndexJob(project, isInitial, triggerSource);
    }

    /**
     * Start a job.
     */
    @Override
    public void startJob(Job job) {
        if (job != null) {
            jobService.startJob(job);
        }
    }

    /**
     * Log a message to a job.
     */
    @Override
    public void logToJob(Job job, JobLogLevel level, String state, String message) {
        if (job != null) {
            jobService.addLog(job, level, state, message, null);
        }
    }

    /**
     * Log a message with metadata to a job.
     */
    @Override
    public void logToJob(Job job, JobLogLevel level, String state, String message, Map<String, Object> metadata) {
        if (job != null) {
            jobService.addLog(job, level, state, message, metadata);
        }
    }

    /**
     * Create an EventConsumer that logs to both the streaming response and the job.
     * Uses the generic WebhookProcessor.EventConsumer interface.
     */
    public WebhookProcessor.EventConsumer createDualConsumer(
            Job job,
            WebhookProcessor.EventConsumer streamConsumer
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
    @Override
    public void completeJob(Job job, Map<String, Object> result) {
        if (job == null) return;
        
        // If result is null, just complete the job successfully
        if (result == null) {
            try {
                jobService.completeJob(job);
            } catch (Exception e) {
                log.error("Error completing job {}", job.getExternalId(), e);
            }
            return;
        }
        
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
                    if (isFailureComment(commentStr)) {
                        isFailed = true;
                        failureReason = commentStr;
                    }
                }
            }
            
            Object topLevelComment = result.get("comment");
            if (!isFailed && topLevelComment instanceof String) {
                String commentStr = (String) topLevelComment;
                if (isFailureComment(commentStr)) {
                    isFailed = true;
                    failureReason = commentStr;
                }
            }
            
            if (!isFailed) {
                Object issues = result.get("issues");
                if (issues instanceof java.util.List) {
                    @SuppressWarnings("unchecked")
                    java.util.List<Object> issueList = (java.util.List<Object>) issues;
                    for (Object issue : issueList) {
                        if (issue instanceof Map) {
                            @SuppressWarnings("unchecked")
                            Map<String, Object> issueMap = (Map<String, Object>) issue;
                            Object file = issueMap.get("file");
                            // System-level issues have file = "system" or "unknown"
                            if ("system".equals(file) || "unknown".equals(file)) {
                                Object reason = issueMap.get("reason");
                                if (reason instanceof String && isFailureComment((String) reason)) {
                                    isFailed = true;
                                    failureReason = (String) reason;
                                    break;
                                }
                            }
                        }
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
     * Check if a comment string indicates a failure condition.
     */
    private boolean isFailureComment(String comment) {
        if (comment == null) return false;
        String lower = comment.toLowerCase();
        return lower.contains("failed to") || 
               lower.contains("failed to parse") ||
               lower.contains("unable to") ||
               lower.contains("cannot perform") ||
               lower.contains("error:") ||
               lower.contains("response parsing failed") ||
               lower.contains("agent returned intermediate tool results") ||
               lower.contains("agent reached its step limit");
    }

    /**
     * Fail a job with an error.
     */
    @Override
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
