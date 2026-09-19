package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Objects;

/** Durable intake and atomic dispatch claims for repository-index work. */
@Service
public class RepositoryIndexJobQueueService {
    private final ProjectRepository projectRepository;
    private final JobRepository jobRepository;
    private final JobService jobService;

    public RepositoryIndexJobQueueService(
            ProjectRepository projectRepository,
            JobRepository jobRepository,
            JobService jobService) {
        this.projectRepository = projectRepository;
        this.jobRepository = jobRepository;
        this.jobService = jobService;
    }

    /**
     * Persist work before dispatch. The project row serializes concurrent event
     * and reconciliation intake without adding a second queue table.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public Job enqueue(
            Project project,
            String branchName,
            String revision,
            JobTriggerSource triggerSource) {
        return enqueue(project, branchName, revision, triggerSource, null);
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public Job enqueue(
            Project project,
            String branchName,
            String revision,
            JobTriggerSource triggerSource,
            String reason) {
        if (project == null || project.getId() == null) {
            throw new IllegalArgumentException("A persisted project is required");
        }
        String branch = requireText(branchName, "branchName");
        String requestedRevision = normalize(revision);
        Project locked = projectRepository.findByIdForUpdate(project.getId())
                .orElseThrow(() -> new IllegalArgumentException(
                        "Project not found: " + project.getId()));
        List<Job> active = jobRepository.findActiveRepositoryIndexJobs(
                locked.getId(), branch);
        JobTriggerSource source = triggerSource != null
                ? triggerSource : JobTriggerSource.SCHEDULED;

        if (source == JobTriggerSource.UI) {
            return enqueueOperatorSuccessor(
                    locked, branch, requestedRevision, active, reason);
        }

        if (requestedRevision == null) {
            return active.isEmpty() ? create(
                    locked, branch, null, source, reason) : active.get(0);
        }

        for (Job job : active) {
            if (Objects.equals(requestedRevision, job.getCommitHash())) {
                return job;
            }
        }

        // Coalesce a successor that has not been claimed. A claimed/running
        // exact revision remains immutable; a newer revision receives one
        // durable successor instead of being lost behind active dedupe.
        for (Job job : active) {
            if (job.getStatus() == JobStatus.PENDING) {
                if (jobRepository.updatePendingRepositoryIndexRevision(
                        job.getId(), requestedRevision, OffsetDateTime.now()) == 1) {
                    return jobRepository.findById(job.getId())
                            .orElseThrow(() -> new IllegalStateException(
                                    "Repository-index successor disappeared: "
                                            + job.getId()));
                }
            }
        }

        return create(locked, branch, requestedRevision, source, reason);
    }

    /**
     * An operator rebuild must remain distinguishable from automatic work so
     * execution cannot reuse the current generation. Reuse an existing UI
     * request, atomically promote an unclaimed automatic successor, or append
     * a new successor behind already-claimed work.
     */
    private Job enqueueOperatorSuccessor(
            Project project,
            String branch,
            String requestedRevision,
            List<Job> active,
            String reason) {
        for (Job job : active) {
            if (job.getTriggerSource() == JobTriggerSource.UI) {
                return job;
            }
        }
        for (Job job : active) {
            if (job.getStatus() == JobStatus.PENDING
                    && jobRepository.promotePendingRepositoryIndexJobToOperator(
                            job.getId(), OffsetDateTime.now()) == 1) {
                if (requestedRevision != null
                        && !Objects.equals(requestedRevision, job.getCommitHash())) {
                    jobRepository.updatePendingRepositoryIndexRevision(
                            job.getId(), requestedRevision, OffsetDateTime.now());
                }
                return jobRepository.findById(job.getId())
                        .orElseThrow(() -> new IllegalStateException(
                                "Repository-index operator successor disappeared: "
                                        + job.getId()));
            }
        }
        return create(
                project, branch, requestedRevision, JobTriggerSource.UI, reason);
    }

    private Job create(
            Project project,
            String branch,
            String revision,
            JobTriggerSource triggerSource,
            String reason) {
        JobTriggerSource source = triggerSource != null
                ? triggerSource : JobTriggerSource.SCHEDULED;
        if (reason == null || reason.isBlank()) {
            return jobService.createRepositoryIndexBuildJob(
                    project, source, null, branch, revision);
        }
        return jobService.createRepositoryIndexBuildJob(
                project, source, null, branch, revision, reason);
    }

    @Transactional(readOnly = true)
    public List<Job> findDispatchCandidates(
            OffsetDateTime queuedBefore,
            int limit) {
        return jobRepository.findRepositoryIndexDispatchCandidates(
                queuedBefore,
                PageRequest.of(0, Math.max(1, limit)));
    }

    @Transactional
    public boolean claim(
            long jobId,
            String expectedRevision,
            String resolvedRevision,
            OffsetDateTime queuedBefore,
            OffsetDateTime claimedAt) {
        return jobRepository.claimRepositoryIndexJob(
                jobId,
                normalize(expectedRevision),
                requireText(resolvedRevision, "resolvedRevision"),
                queuedBefore,
                claimedAt) == 1;
    }

    private static String requireText(String value, String field) {
        String normalized = normalize(value);
        if (normalized == null) {
            throw new IllegalArgumentException(field + " is required");
        }
        return normalized;
    }

    private static String normalize(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }
}
