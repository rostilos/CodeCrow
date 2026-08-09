package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLogLevel;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagBranchIndexKind;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.Executor;
import java.util.function.Consumer;

/**
 * Explicit operator-triggered rebuilds for configured RAG branches.
 *
 * This is deliberately separate from webhook reconciliation: it always builds
 * an exact complete snapshot for the requested revision, and only accepts the
 * primary branch or an explicitly retained target. Each branch build has its
 * own durable operation and job, so an all-branch run is observable and safe
 * to repeat after a partial failure.
 */
@Service
public class BranchIndexMaintenanceService {
    private final RagOperationsService ragOperationsService;
    private final VcsClientProvider vcsClientProvider;
    private final BranchIndexGenerationBuildService generationBuildService;
    private final RagIndexTrackingService trackingService;
    private final AnalysisLockService lockService;
    private final AnalysisJobService jobService;
    private final Executor branchIndexBuildExecutor;
    private final int perProjectParallelism;

    public BranchIndexMaintenanceService(
            RagOperationsService ragOperationsService,
            VcsClientProvider vcsClientProvider,
            BranchIndexGenerationBuildService generationBuildService,
            RagIndexTrackingService trackingService,
            AnalysisLockService lockService,
            AnalysisJobService jobService,
            @Qualifier("branchIndexBuildExecutor") Executor branchIndexBuildExecutor,
            @Value("${codecrow.rag.branch-build.parallelism:2}") int perProjectParallelism) {
        this.ragOperationsService = ragOperationsService;
        this.vcsClientProvider = vcsClientProvider;
        this.generationBuildService = generationBuildService;
        this.trackingService = trackingService;
        this.lockService = lockService;
        this.jobService = jobService;
        this.branchIndexBuildExecutor = branchIndexBuildExecutor;
        this.perProjectParallelism = Math.max(1, perProjectParallelism);
    }

    public Map<String, Object> rebuild(Project project, String requestedBranch, boolean allConfiguredBranches,
            Consumer<Map<String, Object>> events) {
        List<String> branches = resolveBranches(project, requestedBranch, allConfiguredBranches);
        List<String> completed = new ArrayList<>();
        Map<String, String> failures = new LinkedHashMap<>();

        // Obtaining a provider client can refresh a shared installation token. Do
        // that small VCS preparation phase once at a time, then let the expensive
        // archive download and RAG mutation for every resolved branch run in
        // parallel. Concurrent token refreshes previously allowed one branch to
        // disappear before it had a durable job or operation to report.
        List<BranchBuildPlan> plans = new ArrayList<>();
        for (String branch : branches) {
            try {
                plans.add(prepareBuild(project, branch));
            } catch (RuntimeException failure) {
                String message = failure.getMessage() != null
                        ? failure.getMessage() : failure.getClass().getSimpleName();
                failures.put(branch, message);
                events.accept(Map.of("type", "progress", "stage", "branch_failed",
                        "branch", branch,
                        "message", "RAG snapshot failed for branch '" + branch + "': " + message));
            }
        }
        // Limit one project's fan-out independently from the service capacity.
        // A project with many retained branches can therefore use at most its
        // configured share while builds for other tenants still occupy the
        // remaining dedicated RAG slots. Each wave is fully parallel.
        for (int from = 0; from < plans.size(); from += perProjectParallelism) {
            int to = Math.min(plans.size(), from + perProjectParallelism);
            Map<String, CompletableFuture<Void>> wave = new LinkedHashMap<>();
            for (BranchBuildPlan plan : plans.subList(from, to)) {
                String branch = plan.branch();
                wave.put(branch, CompletableFuture.runAsync(() -> {
                    events.accept(Map.of("type", "progress", "stage", "branch",
                            "branch", branch,
                            "message", "Building exact RAG snapshot for branch '" + branch + "'"));
                    rebuildOne(project, plan, events);
                }, branchIndexBuildExecutor));
            }
            for (Map.Entry<String, CompletableFuture<Void>> build : wave.entrySet()) {
                try {
                    build.getValue().join();
                    completed.add(build.getKey());
                } catch (CompletionException failure) {
                    Throwable cause = failure.getCause() != null ? failure.getCause() : failure;
                    String message = cause.getMessage() != null
                            ? cause.getMessage() : cause.getClass().getSimpleName();
                    failures.put(build.getKey(), message);
                    events.accept(Map.of("type", "progress", "stage", "branch_failed",
                            "branch", build.getKey(),
                            "message", "RAG snapshot failed for branch '" + build.getKey()
                                    + "': " + message));
                }
            }
        }
        if (completed.isEmpty()) {
            throw new IllegalStateException("No configured branch snapshot was built: " + failures);
        }
        Map<String, Object> outcome = new LinkedHashMap<>();
        outcome.put("status", "completed");
        outcome.put("message", failures.isEmpty()
                ? "Built RAG snapshots for " + String.join(", ", completed)
                : "Built RAG snapshots for " + String.join(", ", completed)
                        + "; failed: " + String.join(", ", failures.keySet()));
        outcome.put("branches", completed);
        outcome.put("failedBranches", failures);
        return outcome;
    }

    private BranchBuildPlan prepareBuild(Project project, String branch) {
        var binding = project.getVcsRepoBinding();
        if (binding == null || binding.getVcsConnection() == null) {
            throw new IllegalStateException("Project has no VCS connection");
        }
        VcsConnection connection = binding.getVcsConnection();
        String workspace = binding.getExternalNamespace();
        String repository = binding.getExternalRepoSlug();
        VcsClient client = vcsClientProvider.getClient(connection);
        String revision;
        try {
            revision = client.getLatestCommitHash(workspace, repository, branch);
        } catch (Exception failure) {
            throw new IllegalStateException("Could not resolve the current revision for branch '" + branch + "'", failure);
        }
        if (revision == null || revision.isBlank()) {
            throw new IllegalStateException("Branch '" + branch + "' has no resolvable revision");
        }
        return new BranchBuildPlan(branch, connection, workspace, repository, revision);
    }

    private void rebuildOne(Project project, BranchBuildPlan plan, Consumer<Map<String, Object>> events) {
        String branch = plan.branch();
        VcsConnection connection = plan.connection();
        String workspace = plan.workspace();
        String repository = plan.repository();
        String revision = plan.revision();

        Optional<String> lock = lockService.acquireLock(
                project, branch, AnalysisLockType.RAG_INDEXING, revision, null);
        if (lock.isEmpty()) {
            throw new IllegalStateException("RAG indexing is already running for branch '" + branch + "'");
        }

        boolean primary = branch.equals(ragOperationsService.getBaseBranch(project));
        boolean primaryPreviouslyIndexed = primary && trackingService.isProjectIndexed(project);
        Job job = jobService.createRagIndexJob(
                project,
                !primaryPreviouslyIndexed,
                JobTriggerSource.UI,
                branch,
                revision);
        jobService.startJob(job);
        jobService.logToJob(
                job,
                JobLogLevel.INFO,
                "branch_snapshot",
                "Building exact RAG snapshot for branch: " + branch,
                Map.of("branch", branch, "commit", revision));
        try {
            if (primary) {
                trackingService.markIndexingStarted(project, branch, revision);
            }
            var config = project.getConfiguration().ragConfig();
            Map<String, Object> result = generationBuildService.rebuild(
                    project,
                    connection,
                    workspace,
                    repository,
                    branch,
                    revision,
                    primary ? RagBranchIndexKind.PRIMARY : RagBranchIndexKind.DURABLE,
                    config.includePatterns(),
                    config.excludePatterns(),
                    job.getId(), event -> {
                        Map<String, Object> forwarded = new LinkedHashMap<>(event);
                        forwarded.put("type", "progress");
                        forwarded.put("branch", branch);
                        String stage = String.valueOf(
                                forwarded.getOrDefault("stage", "indexing"));
                        String message = String.valueOf(
                                forwarded.getOrDefault("message", "Indexing branch '" + branch + "'"));
                        jobService.logToJob(
                                job,
                                JobLogLevel.INFO,
                                stage,
                                message,
                                forwarded);
                        events.accept(forwarded);
                    });
            if (primary) {
                trackingService.markIndexingCompleted(
                        project,
                        branch,
                        revision,
                        number(result.get("document_count")),
                        number(result.get("chunk_count")));
            }
            jobService.completeJob(job, Map.of("branch", branch, "revision", revision));
            events.accept(Map.of("type", "progress", "stage", "branch_complete", "branch", branch,
                    "message", "RAG snapshot is ready for branch '" + branch + "'"));
        } catch (Throwable failure) {
            String diagnostic = failure.getMessage() != null
                    ? failure.getMessage() : failure.getClass().getSimpleName();
            if (primary) {
                if (primaryPreviouslyIndexed) {
                    trackingService.markIncrementalUpdateFailed(project, diagnostic);
                } else {
                    trackingService.markIndexingFailed(project, diagnostic);
                }
            }
            jobService.failJob(job, diagnostic);
            if (failure instanceof Error error) {
                throw error;
            }
            throw failure instanceof RuntimeException runtime ? runtime
                    : new IllegalStateException("Failed to build RAG snapshot for branch '" + branch + "'", failure);
        } finally {
            lockService.releaseLock(lock.get());
        }
    }

    private List<String> resolveBranches(Project project, String requestedBranch, boolean allConfiguredBranches) {
        if (project.getConfiguration() == null || project.getConfiguration().ragConfig() == null
                || !project.getConfiguration().ragConfig().enabled()) {
            throw new IllegalStateException("RAG is not enabled for this project");
        }
        var config = project.getConfiguration().ragConfig();
        String primary = ragOperationsService.getBaseBranch(project);
        if (allConfiguredBranches) {
            if (!config.isMultiBranchEnabled()) {
                return List.of(primary);
            }
            LinkedHashSet<String> branches = new LinkedHashSet<>();
            branches.add(primary);
            branches.addAll(config.getEffectiveIndexedBranches());
            return List.copyOf(branches);
        }
        if (requestedBranch == null || requestedBranch.isBlank()) {
            throw new IllegalArgumentException("A configured RAG branch must be selected");
        }
        String branch = requestedBranch.trim();
        if (branch.equals(primary)) {
            return List.of(branch);
        }
        if (config.isMultiBranchEnabled() && ragOperationsService.shouldHaveBranchIndex(project, branch)) {
            return List.of(branch);
        }
        throw new IllegalArgumentException("Branch '" + branch + "' is not configured as a retained RAG branch");
    }

    private record BranchBuildPlan(
            String branch,
            VcsConnection connection,
            String workspace,
            String repository,
            String revision) {
    }

    private static int number(Object value) {
        return value instanceof Number number ? number.intValue() : 0;
    }
}
