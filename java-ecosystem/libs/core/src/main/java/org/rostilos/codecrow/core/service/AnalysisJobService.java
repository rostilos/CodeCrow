package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLogLevel;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;

import java.util.Map;

/**
 * Interface for job management operations used by analysis components.
 * This abstraction allows different implementations (pipeline, IDE, CLI) to provide
 * their own job tracking mechanism.
 */
public interface AnalysisJobService {

    /** Create a durable repository-index build bound to one branch revision. */
    Job createRepositoryIndexBuildJob(
            Project project,
            JobTriggerSource triggerSource,
            String branchName,
            String revision);

    /**
     * Start a job.
     * @param job The job to start
     */
    void startJob(Job job);

    /**
     * Log a message to a job.
     * @param job The job to log to
     * @param level The log level
     * @param state The current state/phase
     * @param message The log message
     */
    void logToJob(Job job, JobLogLevel level, String state, String message);

    /**
     * Log a message with metadata to a job.
     * @param job The job to log to
     * @param level The log level
     * @param state The current state/phase
     * @param message The log message
     * @param metadata Additional metadata
     */
    void logToJob(Job job, JobLogLevel level, String state, String message, Map<String, Object> metadata);

    /**
     * Complete a job successfully.
     * @param job The job to complete
     * @param result Optional result data
     */
    void completeJob(Job job, Map<String, Object> result);

    /**
     * Fail a job with an error message.
     * @param job The job to fail
     * @param errorMessage The error message
     */
    void failJob(Job job, String errorMessage);

    /** Finish an intentionally skipped job without representing it as a failure. */
    default void skipJob(Job job, String reason) {
        completeJob(job, Map.of("status", "skipped", "reason", reason));
    }

    /**
     * Announce a success already committed by a fenced repository transition.
     * Persistent hosts should override this without updating terminal status.
     */
    default void recordExternallyCompletedJob(
            Job job,
            String state,
            String message) {
        info(job, state, message);
    }

    /**
     * Log an INFO level message to a job.
     * @param job The job to log to
     * @param state The current state/phase
     * @param message The log message
     */
    default void info(Job job, String state, String message) {
        logToJob(job, JobLogLevel.INFO, state, message);
    }

    /**
     * Log a WARN level message to a job.
     * @param job The job to log to
     * @param state The current state/phase
     * @param message The log message
     */
    default void warn(Job job, String state, String message) {
        logToJob(job, JobLogLevel.WARN, state, message);
    }

    /**
     * Log an ERROR level message to a job.
     * @param job The job to log to
     * @param state The current state/phase
     * @param message The log message
     */
    default void error(Job job, String state, String message) {
        logToJob(job, JobLogLevel.ERROR, state, message);
    }
}
