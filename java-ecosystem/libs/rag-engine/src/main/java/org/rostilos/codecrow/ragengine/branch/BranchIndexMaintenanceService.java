package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.service.RepositoryIndexJobQueueService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Explicit operator-triggered rebuilds for eligible RAG branches.
 *
 * This is deliberately separate from webhook reconciliation: it always builds
 * an exact complete snapshot for the requested revision, and only accepts the
 * primary branch or an analysis-pattern target. Each branch build has its
 * own durable operation and job, so an all-branch run is observable and safe
 * to repeat after a partial failure.
 */
@Service
public class BranchIndexMaintenanceService {
    private static final Logger log = LoggerFactory.getLogger(
            BranchIndexMaintenanceService.class);

    private final RagOperationsService ragOperationsService;
    private final RepositoryIndexJobQueueService queueService;

    public BranchIndexMaintenanceService(
            RagOperationsService ragOperationsService,
            RepositoryIndexJobQueueService queueService) {
        this.ragOperationsService = ragOperationsService;
        this.queueService = queueService;
    }

    public Map<String, Object> rebuild(Project project, String requestedBranch,
            Consumer<Map<String, Object>> events) {
        List<String> branches = resolveBranches(project, requestedBranch);
        List<String> completed = new ArrayList<>();
        Map<String, String> failures = new LinkedHashMap<>();

        for (String branch : branches) {
            try {
                Job queued = queueService.enqueue(
                        project,
                        branch,
                        null,
                        JobTriggerSource.UI);
                completed.add(branch);
                emitEvent(events, Map.of(
                        "type", "progress",
                        "stage", "branch_queued",
                        "branch", branch,
                        "jobId", queued.getExternalId(),
                        "message", "RAG snapshot queued for branch '"
                                + branch + "'"));
            } catch (RuntimeException failure) {
                String message = failure.getMessage() != null
                        ? failure.getMessage() : failure.getClass().getSimpleName();
                failures.put(branch, message);
            }
        }
        if (completed.isEmpty()) {
            throw new IllegalStateException("No repository snapshot was queued: " + failures);
        }
        Map<String, Object> outcome = new LinkedHashMap<>();
        outcome.put("status", "queued");
        outcome.put("message", failures.isEmpty()
                ? "Queued RAG snapshots for " + String.join(", ", completed)
                : "Queued RAG snapshots for " + String.join(", ", completed)
                        + "; failed: " + String.join(", ", failures.keySet()));
        outcome.put("branches", completed);
        outcome.put("failedBranches", failures);
        return outcome;
    }

    private List<String> resolveBranches(Project project, String requestedBranch) {
        if (project.getConfiguration() == null || project.getConfiguration().ragConfig() == null
                || !project.getConfiguration().ragConfig().enabled()) {
            throw new IllegalStateException("RAG is not enabled for this project");
        }
        String primary = ragOperationsService.getBaseBranch(project);
        if (requestedBranch == null || requestedBranch.isBlank()) {
            return List.of(primary);
        }
        String branch = requestedBranch.trim();
        if (branch.equals(primary)) {
            return List.of(branch);
        }
        if (ragOperationsService.shouldHaveBranchIndex(project, branch)) {
            return List.of(branch);
        }
        throw new IllegalArgumentException(
                "Branch '" + branch + "' is outside the configured analysis target/push patterns");
    }

    /** UI delivery is observational and never owns the durable build outcome. */
    private static void emitEvent(
            Consumer<Map<String, Object>> events,
            Map<String, Object> event) {
        if (events == null) {
            return;
        }
        try {
            events.accept(event);
        } catch (RuntimeException observerFailure) {
            log.debug("RAG maintenance observer rejected event stage={}: {}",
                    event.get("stage"), observerFailure.getMessage());
        }
    }
}
