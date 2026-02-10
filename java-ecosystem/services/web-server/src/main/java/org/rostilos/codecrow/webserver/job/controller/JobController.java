package org.rostilos.codecrow.webserver.job.controller;

import org.rostilos.codecrow.core.dto.job.*;
import org.rostilos.codecrow.core.model.job.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.job.JobLogRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/**
 * REST controller for job management and log streaming.
 * Provides endpoints for listing jobs, viewing job details, and streaming logs via SSE.
 */
@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@IsWorkspaceMember
@RequestMapping("/api")
public class JobController {

    private static final Logger log = LoggerFactory.getLogger(JobController.class);
    private static final long SSE_TIMEOUT = 30 * 60 * 1000L; // 30 minutes

    private final JobService jobService;
    private final JobLogRepository jobLogRepository;
    private final ProjectService projectService;
    private final WorkspaceService workspaceService;
    private final ExecutorService sseExecutor = Executors.newCachedThreadPool();

    public JobController(
            JobService jobService,
            JobLogRepository jobLogRepository,
            ProjectService projectService,
            WorkspaceService workspaceService
    ) {
        this.jobService = jobService;
        this.jobLogRepository = jobLogRepository;
        this.projectService = projectService;
        this.workspaceService = workspaceService;
    }

    /**
     * List all jobs for a workspace.
     */
    @GetMapping("/{workspaceSlug}/jobs")
    public ResponseEntity<JobListResponse> listWorkspaceJobs(
            @PathVariable String workspaceSlug,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size,
            @RequestParam(required = false) JobStatus status,
            @RequestParam(required = false) JobType type
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Pageable pageable = PageRequest.of(page, size);

        Page<Job> jobPage = jobService.findByWorkspaceId(workspaceId, pageable);

        List<JobDTO> jobs = jobPage.getContent().stream()
                .map(job -> JobDTO.from(job, jobLogRepository.countByJobId(job.getId())))
                .toList();

        return ResponseEntity.ok(new JobListResponse(
                jobs,
                page,
                size,
                jobPage.getTotalElements(),
                jobPage.getTotalPages()
        ));
    }

    /**
     * List all jobs for a project.
     */
    @GetMapping("/{workspaceSlug}/projects/{projectNamespace}/jobs")
    public ResponseEntity<JobListResponse> listProjectJobs(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size,
            @RequestParam(required = false) JobStatus status,
            @RequestParam(required = false) JobType type
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);

        Pageable pageable = PageRequest.of(page, size);
        Page<Job> jobPage;

        if (status != null) {
            jobPage = jobService.findByProjectIdAndStatus(project.getId(), status, pageable);
        } else if (type != null) {
            jobPage = jobService.findByProjectIdAndJobType(project.getId(), type, pageable);
        } else {
            jobPage = jobService.findByProjectId(project.getId(), pageable);
        }

        List<JobDTO> jobs = jobPage.getContent().stream()
                .map(job -> JobDTO.from(job, jobLogRepository.countByJobId(job.getId())))
                .toList();

        return ResponseEntity.ok(new JobListResponse(
                jobs,
                page,
                size,
                jobPage.getTotalElements(),
                jobPage.getTotalPages()
        ));
    }

    /**
     * Get active (running) jobs for a project.
     */
    @GetMapping("/{workspaceSlug}/projects/{projectNamespace}/jobs/active")
    public ResponseEntity<List<JobDTO>> getActiveJobs(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);

        List<Job> activeJobs = jobService.findActiveJobsByProjectId(project.getId());

        List<JobDTO> jobs = activeJobs.stream()
                .map(job -> JobDTO.from(job, jobLogRepository.countByJobId(job.getId())))
                .toList();

        return ResponseEntity.ok(jobs);
    }

    /**
     * Get job details by external ID.
     */
    @GetMapping("/{workspaceSlug}/projects/{projectNamespace}/jobs/{jobId}")
    public ResponseEntity<JobDTO> getJob(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable String jobId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);

        Job job = jobService.findByExternalIdOrThrow(jobId);

        // Security check: job must belong to the requested project
        if (!job.getProject().getId().equals(project.getId())) {
            return ResponseEntity.notFound().build();
        }

        return ResponseEntity.ok(JobDTO.from(job, jobLogRepository.countByJobId(job.getId())));
    }

    /**
     * Get job logs (paginated or all).
     */
    @GetMapping("/{workspaceSlug}/projects/{projectNamespace}/jobs/{jobId}/logs")
    public ResponseEntity<JobLogsResponse> getJobLogs(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable String jobId,
            @RequestParam(required = false) Long afterSequence
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);

        Job job = jobService.findByExternalIdOrThrow(jobId);

        // Security check
        if (!job.getProject().getId().equals(project.getId())) {
            return ResponseEntity.notFound().build();
        }

        List<JobLog> logs;
        if (afterSequence != null && afterSequence > 0) {
            logs = jobService.getJobLogsAfterSequence(job.getId(), afterSequence);
        } else {
            logs = jobService.getJobLogs(job.getId());
        }

        List<JobLogDTO> logDTOs = logs.stream()
                .map(JobLogDTO::from)
                .toList();

        Long latestSequence = jobService.getLatestSequenceNumber(job.getId());

        return ResponseEntity.ok(new JobLogsResponse(
                jobId,
                logDTOs,
                latestSequence,
                job.isTerminal()
        ));
    }

    /**
     * Stream job logs via Server-Sent Events (SSE).
     * Clients can use this for real-time log updates.
     */
    @GetMapping(value = "/{workspaceSlug}/projects/{projectNamespace}/jobs/{jobId}/logs/stream",
            produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter streamJobLogs(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable String jobId,
            @RequestParam(required = false, defaultValue = "0") Long afterSequence
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);

        Job job = jobService.findByExternalIdOrThrow(jobId);

        // Security check
        if (!job.getProject().getId().equals(project.getId())) {
            SseEmitter emitter = new SseEmitter(0L);
            emitter.completeWithError(new SecurityException("Access denied"));
            return emitter;
        }

        SseEmitter emitter = new SseEmitter(SSE_TIMEOUT);

        // Send existing logs first
        sseExecutor.execute(() -> {
            try {
                // Send existing logs after the specified sequence
                List<JobLog> existingLogs = jobService.getJobLogsAfterSequence(job.getId(), afterSequence);
                for (JobLog logEntry : existingLogs) {
                    emitter.send(SseEmitter.event()
                            .name("log")
                            .data(JobLogDTO.from(logEntry)));
                }

                // If job is already complete, send completion and close
                if (job.isTerminal()) {
                    emitter.send(SseEmitter.event()
                            .name("complete")
                            .data(Map.of(
                                    "status", job.getStatus().name(),
                                    "message", job.getStatus() == JobStatus.FAILED ? job.getErrorMessage() : "Job completed"
                            )));
                    emitter.complete();
                    return;
                }

                // Subscribe to new logs
                Consumer<JobLog> subscriber = logEntry -> {
                    try {
                        emitter.send(SseEmitter.event()
                                .name("log")
                                .data(JobLogDTO.from(logEntry)));
                    } catch (IOException e) {
                        log.debug("SSE send failed for job {}", jobId);
                    }
                };

                jobService.subscribe(jobId, subscriber);

                // Setup cleanup on completion/timeout/error
                emitter.onCompletion(() -> {
                    jobService.unsubscribe(jobId, subscriber);
                    log.debug("SSE completed for job {}", jobId);
                });

                emitter.onTimeout(() -> {
                    jobService.unsubscribe(jobId, subscriber);
                    log.debug("SSE timeout for job {}", jobId);
                    emitter.complete();
                });

                emitter.onError(e -> {
                    jobService.unsubscribe(jobId, subscriber);
                    log.debug("SSE error for job {}: {}", jobId, e.getMessage());
                });

            } catch (Exception e) {
                log.error("Error setting up SSE for job {}", jobId, e);
                emitter.completeWithError(e);
            }
        });

        return emitter;
    }

    /**
     * Cancel a running job.
     */
    @PostMapping("/{workspaceSlug}/projects/{projectNamespace}/jobs/{jobId}/cancel")
    public ResponseEntity<JobDTO> cancelJob(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable String jobId
    ) {
        Long workspaceId = workspaceService.getWorkspaceBySlug(workspaceSlug).getId();
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspaceId, projectNamespace);

        Job job = jobService.findByExternalIdOrThrow(jobId);

        // Security check
        if (!job.getProject().getId().equals(project.getId())) {
            return ResponseEntity.notFound().build();
        }

        if (job.isTerminal()) {
            return ResponseEntity.badRequest().build();
        }

        job = jobService.cancelJob(job);
        return ResponseEntity.ok(JobDTO.from(job, jobLogRepository.countByJobId(job.getId())));
    }
}
