package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLogLevel;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.reconcile.ReconcileTask;
import org.rostilos.codecrow.core.model.reconcile.ReconcileTaskStatus;
import org.rostilos.codecrow.core.persistence.repository.reconcile.ReconcileTaskRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Scheduled service that polls for PENDING {@link ReconcileTask} rows
 * created by the web-server and executes full branch reconciliation.
 * <p>
 * Architecture:
 * <ol>
 *   <li>Web-server creates a {@code ReconcileTask} with status PENDING</li>
 *   <li>This scheduler picks it up, sets IN_PROGRESS, calls
 *       {@link BranchAnalysisProcessor#fullReconcile}</li>
 *   <li>On success → COMPLETED with result metrics; on failure → FAILED with error</li>
 * </ol>
 * Each task also creates a {@link Job} record so the operation is visible
 * in the Jobs UI.
 */
@Service
public class ReconcileTaskScheduler {

    private static final Logger log = LoggerFactory.getLogger(ReconcileTaskScheduler.class);

    /** Maximum tasks to process per poll cycle. */
    private static final int BATCH_SIZE = 3;

    /** Timeout threshold: if a task has been IN_PROGRESS for longer than this, mark it FAILED. */
    private static final long STUCK_THRESHOLD_MINUTES = 30;

    private final ReconcileTaskRepository reconcileTaskRepository;
    private final BranchAnalysisProcessor branchAnalysisProcessor;
    private final ProjectRepository projectRepository;
    private final JobService jobService;

    public ReconcileTaskScheduler(
            ReconcileTaskRepository reconcileTaskRepository,
            BranchAnalysisProcessor branchAnalysisProcessor,
            ProjectRepository projectRepository,
            JobService jobService
    ) {
        this.reconcileTaskRepository = reconcileTaskRepository;
        this.branchAnalysisProcessor = branchAnalysisProcessor;
        this.projectRepository = projectRepository;
        this.jobService = jobService;
    }

    /**
     * Poll for pending reconcile tasks every 15 seconds.
     */
    @Scheduled(fixedDelayString = "${reconcile.task.poll.interval.ms:15000}")
    @Transactional
    public void pollAndExecuteReconcileTasks() {
        // First, clean up stuck tasks
        recoverStuckTasks();

        List<ReconcileTask> pendingTasks = reconcileTaskRepository
                .findByStatusOrderByCreatedAtAsc(ReconcileTaskStatus.PENDING);

        if (pendingTasks.isEmpty()) {
            return;
        }

        log.info("Found {} pending reconcile task(s)", pendingTasks.size());

        int processed = 0;
        for (ReconcileTask task : pendingTasks) {
            if (processed >= BATCH_SIZE) {
                log.info("Batch limit reached ({}), remaining tasks will be processed next cycle", BATCH_SIZE);
                break;
            }

            executeTask(task);
            processed++;
        }
    }

    /**
     * Execute a single reconcile task and track it as a Job.
     */
    private void executeTask(ReconcileTask task) {
        log.info("Starting reconcile task: id={}, externalId={}, project={}, branch='{}'",
                task.getId(), task.getExternalId(), task.getProjectId(), task.getBranchName());

        // Mark as IN_PROGRESS
        task.markInProgress();
        reconcileTaskRepository.save(task);

        // Look up the Project entity for Job creation
        Optional<Project> projectOpt = projectRepository.findById(task.getProjectId());
        if (projectOpt.isEmpty()) {
            log.error("Project not found for reconcile task: projectId={}", task.getProjectId());
            task.markFailed("Project not found: " + task.getProjectId());
            reconcileTaskRepository.save(task);
            return;
        }
        Project project = projectOpt.get();

        // Create a Job so the reconciliation is visible in the Jobs UI
        Job job = jobService.createBranchReconciliationJob(
                project, task.getBranchName(), JobTriggerSource.UI, null);
        job = jobService.startJob(job);
        jobService.addLog(job, JobLogLevel.INFO, "reconcile",
                "Full reconciliation started for branch: " + task.getBranchName());

        try {
            Map<String, Object> result = branchAnalysisProcessor.fullReconcile(
                    task.getProjectId(),
                    task.getBranchName(),
                    event -> { /* discard SSE events for background task */ });

            int totalIssues = toInt(result.get("totalIssues"));
            int resolvedIssues = toInt(result.get("resolvedIssues"));
            int filesChecked = toInt(result.get("filesChecked"));
            String message = String.valueOf(result.getOrDefault("message",
                    "Reconciliation completed successfully"));

            task.markCompleted(totalIssues, resolvedIssues, filesChecked, message);
            reconcileTaskRepository.save(task);

            // Complete the Job
            jobService.addLog(job, JobLogLevel.INFO, "reconcile",
                    String.format("Reconciliation complete: %d total issues, %d resolved, %d files checked",
                            totalIssues, resolvedIssues, filesChecked));
            job = jobService.completeJob(job);

            log.info("Reconcile task completed: externalId={}, total={}, resolved={}, files={}",
                    task.getExternalId(), totalIssues, resolvedIssues, filesChecked);

        } catch (Exception e) {
            log.error("Reconcile task failed: externalId={}, error={}",
                    task.getExternalId(), e.getMessage(), e);

            task.markFailed(e.getMessage());
            reconcileTaskRepository.save(task);

            // Fail the Job
            jobService.addLog(job, JobLogLevel.ERROR, "reconcile",
                    "Reconciliation failed: " + e.getMessage());
            job = jobService.failJob(job, e.getMessage());
        }
    }

    /**
     * Recover tasks that have been IN_PROGRESS for too long (likely crashed).
     */
    private void recoverStuckTasks() {
        OffsetDateTime threshold = OffsetDateTime.now().minusMinutes(STUCK_THRESHOLD_MINUTES);
        List<ReconcileTask> stuckTasks = reconcileTaskRepository.findStuckTasks(threshold);

        for (ReconcileTask stuck : stuckTasks) {
            log.warn("Recovering stuck reconcile task: externalId={}, startedAt={}",
                    stuck.getExternalId(), stuck.getStartedAt());
            stuck.markFailed("Task timed out after " + STUCK_THRESHOLD_MINUTES + " minutes — "
                    + "it may have been interrupted by a service restart");
            reconcileTaskRepository.save(stuck);
        }
    }

    private static int toInt(Object value) {
        if (value instanceof Number) {
            return ((Number) value).intValue();
        }
        return 0;
    }
}
