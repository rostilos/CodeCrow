package org.rostilos.codecrow.core.dto.job;

import org.rostilos.codecrow.core.model.job.*;

import java.time.OffsetDateTime;

public record JobDTO(
        String id,
        Long projectId,
        String projectName,
        String projectNamespace,
        Long workspaceId,
        String workspaceName,
        Long triggeredByUserId,
        String triggeredByUsername,
        JobType jobType,
        JobStatus status,
        JobTriggerSource triggerSource,
        String title,
        String branchName,
        Long prNumber,
        String commitHash,
        Long codeAnalysisId,
        String errorMessage,
        Integer progress,
        String currentStep,
        OffsetDateTime createdAt,
        OffsetDateTime startedAt,
        OffsetDateTime completedAt,
        Long durationMs,
        Long logCount
) {
    /**
     * Create DTO from Job entity.
     */
    public static JobDTO from(Job job) {
        return from(job, null);
    }

    /**
     * Create DTO from Job entity with log count.
     */
    public static JobDTO from(Job job, Long logCount) {
        Long durationMs = null;
        if (job.getStartedAt() != null && job.getCompletedAt() != null) {
            durationMs = java.time.Duration.between(job.getStartedAt(), job.getCompletedAt()).toMillis();
        } else if (job.getStartedAt() != null) {
            durationMs = java.time.Duration.between(job.getStartedAt(), OffsetDateTime.now()).toMillis();
        }

        return new JobDTO(
                job.getExternalId(),
                job.getProject().getId(),
                job.getProject().getName(),
                job.getProject().getNamespace(),
                job.getProject().getWorkspace().getId(),
                job.getProject().getWorkspace().getName(),
                job.getTriggeredBy() != null ? job.getTriggeredBy().getId() : null,
                job.getTriggeredBy() != null ? job.getTriggeredBy().getUsername() : null,
                job.getJobType(),
                job.getStatus(),
                job.getTriggerSource(),
                job.getTitle(),
                job.getBranchName(),
                job.getPrNumber(),
                job.getCommitHash(),
                job.getCodeAnalysis() != null ? job.getCodeAnalysis().getId() : null,
                job.getErrorMessage(),
                job.getProgress(),
                job.getCurrentStep(),
                job.getCreatedAt(),
                job.getStartedAt(),
                job.getCompletedAt(),
                durationMs,
                logCount
        );
    }
}
