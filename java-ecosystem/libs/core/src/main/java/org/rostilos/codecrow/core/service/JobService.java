package org.rostilos.codecrow.core.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.persistence.repository.job.JobLogRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;

/**
 * Service for managing background jobs and their logs.
 * Provides functionality for creating, tracking, and logging jobs.
 */
@Service
public class JobService {

    private static final Logger log = LoggerFactory.getLogger(JobService.class);

    private final JobRepository jobRepository;
    private final JobLogRepository jobLogRepository;
    private final ObjectMapper objectMapper;

    /**
     * Map of job external ID to list of SSE subscribers for real-time log streaming.
     */
    private final ConcurrentHashMap<String, List<Consumer<JobLog>>> sseSubscribers = new ConcurrentHashMap<>();

    public JobService(
            JobRepository jobRepository,
            JobLogRepository jobLogRepository,
            ObjectMapper objectMapper
    ) {
        this.jobRepository = jobRepository;
        this.jobLogRepository = jobLogRepository;
        this.objectMapper = objectMapper;
    }

    // ==================== Job Creation ====================

    /**
     * Create a new job for PR analysis.
     */
    @Transactional
    public Job createPrAnalysisJob(
            Project project,
            Long prNumber,
            String sourceBranch,
            String targetBranch,
            String commitHash,
            JobTriggerSource triggerSource,
            User triggeredBy
    ) {
        Job job = new Job();
        job.setProject(project);
        job.setJobType(JobType.PR_ANALYSIS);
        job.setTriggerSource(triggerSource);
        job.setTriggeredBy(triggeredBy);
        job.setPrNumber(prNumber);
        job.setBranchName(targetBranch);
        job.setCommitHash(commitHash);
        job.setTitle(String.format("PR #%d Analysis: %s → %s", prNumber, sourceBranch, targetBranch));
        job.setStatus(JobStatus.PENDING);

        job = jobRepository.save(job);
        addLog(job, JobLogLevel.INFO, "init", "Job created for PR #" + prNumber);

        return job;
    }

    /**
     * Create a new job for branch analysis (push event).
     */
    @Transactional
    public Job createBranchAnalysisJob(
            Project project,
            String branchName,
            String commitHash,
            JobTriggerSource triggerSource,
            User triggeredBy
    ) {
        Job job = new Job();
        job.setProject(project);
        job.setJobType(JobType.BRANCH_ANALYSIS);
        job.setTriggerSource(triggerSource);
        job.setTriggeredBy(triggeredBy);
        job.setBranchName(branchName);
        job.setCommitHash(commitHash);
        job.setTitle(String.format("Branch Analysis: %s", branchName));
        job.setStatus(JobStatus.PENDING);

        job = jobRepository.save(job);
        addLog(job, JobLogLevel.INFO, "init", "Job created for branch: " + branchName);

        return job;
    }

    /**
     * Create a new job for RAG indexing.
     */
    @Transactional
    public Job createRagIndexJob(
            Project project,
            boolean isInitial,
            JobTriggerSource triggerSource,
            User triggeredBy
    ) {
        Job job = new Job();
        job.setProject(project);
        job.setJobType(isInitial ? JobType.RAG_INITIAL_INDEX : JobType.RAG_INCREMENTAL_INDEX);
        job.setTriggerSource(triggerSource);
        job.setTriggeredBy(triggeredBy);
        job.setTitle(isInitial ? "Initial RAG Indexing" : "Incremental RAG Update");
        job.setStatus(JobStatus.PENDING);

        job = jobRepository.save(job);
        addLog(job, JobLogLevel.INFO, "init", "RAG indexing job created");

        return job;
    }

    // ==================== Job Lifecycle ====================

    /**
     * Start a job (transition from PENDING to RUNNING).
     */
    @Transactional
    public Job startJob(Job job) {
        job.start();
        job = jobRepository.save(job);
        addLog(job, JobLogLevel.INFO, "start", "Job started");
        return job;
    }

    /**
     * Start a job by external ID.
     */
    @Transactional
    public Job startJob(String externalId) {
        Job job = findByExternalIdOrThrow(externalId);
        return startJob(job);
    }

    /**
     * Complete a job successfully.
     */
    @Transactional
    public Job completeJob(Job job) {
        job.complete();
        job = jobRepository.save(job);
        addLog(job, JobLogLevel.INFO, "complete", "Job completed successfully");
        notifyJobComplete(job);
        return job;
    }

    /**
     * Complete a job and link it to a code analysis.
     */
    @Transactional
    public Job completeJob(Job job, CodeAnalysis codeAnalysis) {
        job.setCodeAnalysis(codeAnalysis);
        job.complete();
        job = jobRepository.save(job);
        addLog(job, JobLogLevel.INFO, "complete",
                String.format("Job completed. Analysis ID: %d, Issues found: %d",
                        codeAnalysis.getId(), codeAnalysis.getTotalIssues()));
        notifyJobComplete(job);
        return job;
    }

    /**
     * Fail a job with an error message.
     * Uses REQUIRES_NEW to ensure this runs in its own transaction,
     * allowing it to work even if the calling transaction has failed.
     */
    @Transactional(propagation = org.springframework.transaction.annotation.Propagation.REQUIRES_NEW)
    public Job failJob(Job job, String errorMessage) {
        // Re-fetch the job to ensure we have a fresh entity in this new transaction
        job = jobRepository.findById(job.getId()).orElse(job);
        job.fail(errorMessage);
        job = jobRepository.save(job);
        addLogInNewTransaction(job, JobLogLevel.ERROR, "error", "Job failed: " + errorMessage);
        notifyJobComplete(job);
        return job;
    }

    /**
     * Cancel a job.
     */
    @Transactional
    public Job cancelJob(Job job) {
        job.cancel();
        job = jobRepository.save(job);
        addLog(job, JobLogLevel.WARN, "cancel", "Job cancelled");
        notifyJobComplete(job);
        return job;
    }

    /**
     * Update job progress.
     */
    @Transactional
    public Job updateProgress(Job job, int progress, String currentStep) {
        job.setProgress(progress);
        job.setCurrentStep(currentStep);
        job = jobRepository.save(job);
        addLog(job, JobLogLevel.INFO, currentStep,
                String.format("Progress: %d%% - %s", progress, currentStep));
        return job;
    }

    // ==================== Logging ====================

    /**
     * Add a log entry to a job.
     */
    @Transactional
    public JobLog addLog(Job job, JobLogLevel level, String step, String message) {
        JobLog logEntry = new JobLog();
        logEntry.setJob(job);
        logEntry.setLevel(level);
        logEntry.setStep(step);
        logEntry.setMessage(message);
        logEntry.setSequenceNumber(jobLogRepository.getNextSequenceNumber(job.getId()));

        logEntry = jobLogRepository.save(logEntry);

        // Notify SSE subscribers
        notifySubscribers(job.getExternalId(), logEntry);

        log.debug("[Job {}] {} [{}] {}", job.getExternalId(), level, step, message);

        return logEntry;
    }

    /**
     * Add a log entry in a new transaction.
     * Used when the calling transaction may have failed.
     */
    @Transactional(propagation = org.springframework.transaction.annotation.Propagation.REQUIRES_NEW)
    public JobLog addLogInNewTransaction(Job job, JobLogLevel level, String step, String message) {
        JobLog logEntry = new JobLog();
        logEntry.setJob(job);
        logEntry.setLevel(level);
        logEntry.setStep(step);
        logEntry.setMessage(message);
        logEntry.setSequenceNumber(jobLogRepository.getNextSequenceNumber(job.getId()));

        logEntry = jobLogRepository.save(logEntry);

        // Notify SSE subscribers
        notifySubscribers(job.getExternalId(), logEntry);

        log.debug("[Job {}] {} [{}] {}", job.getExternalId(), level, step, message);

        return logEntry;
    }

    /**
     * Add a log entry with metadata.
     */
    @Transactional
    public JobLog addLog(Job job, JobLogLevel level, String step, String message, Map<String, Object> metadata) {
        JobLog logEntry = new JobLog();
        logEntry.setJob(job);
        logEntry.setLevel(level);
        logEntry.setStep(step);
        logEntry.setMessage(message);
        logEntry.setSequenceNumber(jobLogRepository.getNextSequenceNumber(job.getId()));

        try {
            logEntry.setMetadata(objectMapper.writeValueAsString(metadata));
        } catch (JsonProcessingException e) {
            log.warn("Failed to serialize log metadata", e);
        }

        logEntry = jobLogRepository.save(logEntry);
        notifySubscribers(job.getExternalId(), logEntry);

        return logEntry;
    }

    /**
     * Add an info log.
     */
    @Transactional
    public JobLog info(Job job, String step, String message) {
        return addLog(job, JobLogLevel.INFO, step, message);
    }

    /**
     * Add a warning log.
     */
    @Transactional
    public JobLog warn(Job job, String step, String message) {
        return addLog(job, JobLogLevel.WARN, step, message);
    }

    /**
     * Add an error log.
     */
    @Transactional
    public JobLog error(Job job, String step, String message) {
        return addLog(job, JobLogLevel.ERROR, step, message);
    }

    /**
     * Add a debug log.
     */
    @Transactional
    public JobLog debug(Job job, String step, String message) {
        return addLog(job, JobLogLevel.DEBUG, step, message);
    }

    // ==================== Queries ====================

    public Optional<Job> findByExternalId(String externalId) {
        return jobRepository.findByExternalId(externalId);
    }

    public Job findByExternalIdOrThrow(String externalId) {
        return jobRepository.findByExternalId(externalId)
                .orElseThrow(() -> new IllegalArgumentException("Job not found: " + externalId));
    }

    public Optional<Job> findById(Long id) {
        return jobRepository.findById(id);
    }

    public Page<Job> findByProjectId(Long projectId, Pageable pageable) {
        return jobRepository.findByProjectId(projectId, pageable);
    }

    public Page<Job> findByWorkspaceId(Long workspaceId, Pageable pageable) {
        return jobRepository.findByWorkspaceId(workspaceId, pageable);
    }

    public Page<Job> findByProjectIdAndStatus(Long projectId, JobStatus status, Pageable pageable) {
        return jobRepository.findByProjectIdAndStatus(projectId, status, pageable);
    }

    public Page<Job> findByProjectIdAndJobType(Long projectId, JobType jobType, Pageable pageable) {
        return jobRepository.findByProjectIdAndJobType(projectId, jobType, pageable);
    }

    public List<Job> findActiveJobsByProjectId(Long projectId) {
        return jobRepository.findActiveJobsByProjectId(projectId);
    }

    public Optional<Job> findByCodeAnalysisId(Long analysisId) {
        return jobRepository.findByCodeAnalysisId(analysisId);
    }

    public Optional<Job> findLatestJobForPr(Long projectId, Long prNumber) {
        List<Job> jobs = jobRepository.findLatestJobsForPr(projectId, prNumber, PageRequest.of(0, 1));
        return jobs.isEmpty() ? Optional.empty() : Optional.of(jobs.get(0));
    }

    // ==================== Log Queries ====================

    public List<JobLog> getJobLogs(Long jobId) {
        return jobLogRepository.findByJobIdOrderBySequence(jobId);
    }

    public Page<JobLog> getJobLogs(Long jobId, Pageable pageable) {
        return jobLogRepository.findByJobId(jobId, pageable);
    }

    public List<JobLog> getJobLogsAfterSequence(Long jobId, Long afterSequence) {
        return jobLogRepository.findByJobIdAfterSequence(jobId, afterSequence);
    }

    public Long getLatestSequenceNumber(Long jobId) {
        return jobLogRepository.getLatestSequenceNumber(jobId);
    }

    // ==================== SSE Subscription ====================

    /**
     * Subscribe to real-time log updates for a job.
     */
    public void subscribe(String jobExternalId, Consumer<JobLog> subscriber) {
        sseSubscribers.computeIfAbsent(jobExternalId, k -> new java.util.concurrent.CopyOnWriteArrayList<>())
                .add(subscriber);
    }

    /**
     * Unsubscribe from log updates.
     */
    public void unsubscribe(String jobExternalId, Consumer<JobLog> subscriber) {
        List<Consumer<JobLog>> subscribers = sseSubscribers.get(jobExternalId);
        if (subscribers != null) {
            subscribers.remove(subscriber);
            if (subscribers.isEmpty()) {
                sseSubscribers.remove(jobExternalId);
            }
        }
    }

    private void notifySubscribers(String jobExternalId, JobLog logEntry) {
        List<Consumer<JobLog>> subscribers = sseSubscribers.get(jobExternalId);
        if (subscribers != null) {
            for (Consumer<JobLog> subscriber : subscribers) {
                try {
                    subscriber.accept(logEntry);
                } catch (Exception e) {
                    log.warn("Failed to notify subscriber for job {}", jobExternalId, e);
                }
            }
        }
    }

    private void notifyJobComplete(Job job) {
        // Remove subscribers when job completes
        sseSubscribers.remove(job.getExternalId());
    }

    // ==================== Cleanup ====================

    /**
     * Clean up old completed jobs (older than the specified threshold).
     */
    @Transactional
    public void cleanupOldJobs(OffsetDateTime threshold) {
        jobRepository.deleteOldCompletedJobs(threshold);
    }

    /**
     * Find and fail stuck jobs (running for too long).
     */
    @Transactional
    public List<Job> findAndFailStuckJobs(OffsetDateTime threshold, String reason) {
        List<Job> stuckJobs = jobRepository.findStuckJobs(threshold);
        for (Job job : stuckJobs) {
            failJob(job, reason);
        }
        return stuckJobs;
    }
}
