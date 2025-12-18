package org.rostilos.codecrow.analysisengine.service.job;

import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLogLevel;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.user.User;

import java.util.Map;

/**
 * Interface for job management operations used by analysis-engine components.
 * This abstraction allows different implementations (pipeline, IDE, CLI) to provide
 * their own job tracking mechanism.
 */
public interface AnalysisJobService {

    /**
     * Create a job for RAG initial indexing.
     * @param project The project to index
     * @param triggeredBy The user who triggered the job (can be null for automated jobs)
     * @return The created job
     */
    Job createRagIndexJob(Project project, User triggeredBy);

    /**
     * Create a job for RAG indexing with configurable parameters.
     * @param project The project to index
     * @param isInitial true for initial indexing, false for incremental update
     * @param triggerSource The source that triggered the job
     * @return The created job
     */
    Job createRagIndexJob(Project project, boolean isInitial, JobTriggerSource triggerSource);

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
