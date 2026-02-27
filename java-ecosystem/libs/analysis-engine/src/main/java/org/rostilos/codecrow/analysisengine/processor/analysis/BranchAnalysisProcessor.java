package org.rostilos.codecrow.analysisengine.processor.analysis;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.processor.analysis.branch.BranchFileOperationsService;
import org.rostilos.codecrow.analysisengine.processor.analysis.branch.BranchIssueMappingService;
import org.rostilos.codecrow.analysisengine.processor.analysis.branch.BranchIssueReconciliationService;
import org.rostilos.codecrow.analysisengine.processor.analysis.branch.DiffParsingUtils;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisengine.service.gitgraph.CommitCoverageService;
import org.rostilos.codecrow.analysisengine.service.gitgraph.GitGraphSyncService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.gitgraph.CommitNode;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.events.EventNotificationEmitter;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.*;
import java.util.function.Consumer;
import java.util.stream.Collectors;

/**
 * Orchestrates branch analysis after PR merges or direct commits.
 * <p>
 * This is a <em>thin orchestrator</em> that delegates to focused services:
 * <ul>
 *   <li>{@link BranchFileOperationsService} — file-level ops (archive, snapshots, branch files)</li>
 *   <li>{@link BranchIssueMappingService} — CAI → BranchIssue mapping with dedup</li>
 *   <li>{@link BranchIssueReconciliationService} — tracking, reconciliation, AI fallback</li>
 *   <li>{@link DiffParsingUtils} — stateless diff parsing utilities</li>
 * </ul>
 */
@Service
public class BranchAnalysisProcessor {

    private static final Logger log = LoggerFactory.getLogger(BranchAnalysisProcessor.class);

    // ── Core dependencies ───────────────────────────────────────────────────
    private final ProjectService projectService;
    private final BranchRepository branchRepository;
    private final VcsClientProvider vcsClientProvider;
    private final VcsServiceFactory vcsServiceFactory;
    private final AnalysisLockService analysisLockService;
    private final GitGraphSyncService gitGraphSyncService;

    // ── Extracted domain services ───────────────────────────────────────────
    private final BranchFileOperationsService branchFileOperationsService;
    private final BranchIssueMappingService branchIssueMappingService;
    private final BranchIssueReconciliationService branchIssueReconciliationService;

    // ── Hybrid (direct push) analysis dependencies ──────────────────────────
    private final CommitCoverageService commitCoverageService;
    private final CodeAnalysisService codeAnalysisService;
    private final AiAnalysisClient aiAnalysisClient;
    private final PullRequestService pullRequestService;

    /** Optional RAG operations service — can be null if RAG module is not deployed. */
    private final RagOperationsService ragOperationsService;

    /** Helper record to hold VCS connection info. */
    public record VcsInfo(VcsConnection vcsConnection, String workspace, String repoSlug) {
    }

    public BranchAnalysisProcessor(
            ProjectService projectService,
            BranchRepository branchRepository,
            VcsClientProvider vcsClientProvider,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService,
            GitGraphSyncService gitGraphSyncService,
            BranchFileOperationsService branchFileOperationsService,
            BranchIssueMappingService branchIssueMappingService,
            BranchIssueReconciliationService branchIssueReconciliationService,
            CommitCoverageService commitCoverageService,
            CodeAnalysisService codeAnalysisService,
            AiAnalysisClient aiAnalysisClient,
            PullRequestService pullRequestService,
            @Autowired(required = false) RagOperationsService ragOperationsService) {
        this.projectService = projectService;
        this.branchRepository = branchRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.vcsServiceFactory = vcsServiceFactory;
        this.analysisLockService = analysisLockService;
        this.gitGraphSyncService = gitGraphSyncService;
        this.branchFileOperationsService = branchFileOperationsService;
        this.branchIssueMappingService = branchIssueMappingService;
        this.branchIssueReconciliationService = branchIssueReconciliationService;
        this.commitCoverageService = commitCoverageService;
        this.codeAnalysisService = codeAnalysisService;
        this.aiAnalysisClient = aiAnalysisClient;
        this.pullRequestService = pullRequestService;
        this.ragOperationsService = ragOperationsService;
    }

    // ════════════════════════════ VCS helpers ════════════════════════════════

    /**
     * Resolve VCS connection info from a project's effective repo configuration.
     */
    public VcsInfo getVcsInfo(Project project) {
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null && vcsInfo.getVcsConnection() != null) {
            return new VcsInfo(
                    vcsInfo.getVcsConnection(),
                    vcsInfo.getRepoWorkspace(),
                    vcsInfo.getRepoSlug());
        }
        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    private EVcsProvider getVcsProvider(Project project) {
        return getVcsInfo(project).vcsConnection().getProviderType();
    }

    // ════════════════════════ Diff parsing delegate ══════════════════════════

    /**
     * Parse changed file paths from a unified diff.
     * Delegates to {@link DiffParsingUtils#parseFilePathsFromDiff(String)}.
     */
    public Set<String> parseFilePathsFromDiff(String rawDiff) {
        return DiffParsingUtils.parseFilePathsFromDiff(rawDiff);
    }

    // ═══════════════════════ Main analysis pipeline ══════════════════════════

    /**
     * Primary entry point — run branch analysis for a given request.
     *
     * @param request  the branch analysis request
     * @param consumer SSE event consumer for progress updates
     * @return result map with status/metadata
     */
    public Map<String, Object> process(BranchProcessRequest request,
                                        Consumer<Map<String, Object>> consumer)
            throws IOException {

        Project project = projectService.getProjectWithConnections(request.getProjectId());

        Optional<String> lockKey = analysisLockService.acquireLockWithWait(
                project, request.getTargetBranchName(), AnalysisLockType.BRANCH_ANALYSIS,
                request.getCommitHash(), null, consumer);

        if (lockKey.isEmpty()) {
            log.warn("Branch analysis already in progress for project={}, branch={}",
                    project.getId(), request.getTargetBranchName());
            throw new AnalysisLockedException(
                    AnalysisLockType.BRANCH_ANALYSIS.name(),
                    request.getTargetBranchName(), project.getId());
        }

        List<String> unanalyzedCommits = Collections.emptyList();

        try {
            Optional<Branch> existingBranchOpt = branchRepository.findByProjectIdAndBranchName(
                    project.getId(), request.getTargetBranchName());

            if (matchCache(request, existingBranchOpt, project, consumer)) {
                return Map.of(
                        "status", "skipped",
                        "reason", "commit_already_analyzed",
                        "branch", request.getTargetBranchName(),
                        "commitHash", request.getCommitHash());
            }

            EventNotificationEmitter.emitStatus(consumer, "started",
                    "Branch analysis started for branch: " + request.getTargetBranchName());

            VcsInfo vcsInfo = getVcsInfo(project);
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());
            EVcsProvider provider = getVcsProvider(project);
            VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);

            // ── DAG-driven commit graph analysis ─────────────────────────────
            DagContext dagCtx = syncDag(project, vcsInfo, request);
            unanalyzedCommits = dagCtx.unanalyzedCommits;

            if (dagCtx.skipAnalysis) {
                EventNotificationEmitter.emitStatus(consumer, "skipped","All commits already analyzed (DAG check)");
                return Map.of("status", "skipped", "reason", "dag_already_analyzed",
                        "branch", request.getTargetBranchName(),
                        "commitHash", request.getCommitHash());
            }
            
            EventNotificationEmitter.emitStatus(consumer, "fetching_diff", "Fetching diff for analysis");

            // ── PR number resolution ─────────────────────────────────────────
            Long prNumber = resolvePrNumber(request, operationsService, client, vcsInfo);

            // Mark the source PR as MERGED if this branch analysis was triggered by a PR merge.
            // This keeps PullRequestState accurate for commit coverage checks.
            if (prNumber != null) {
                try {
                    pullRequestService.markPullRequestMerged(project.getId(), prNumber);
                } catch (Exception e) {
                    log.debug("Could not mark PR #{} as merged (may not exist yet): {}", prNumber, e.getMessage());
                }
            }

            // ── Multi-tier diff strategy ─────────────────────────────────────
            String rawDiff = fetchDiff(request, existingBranchOpt.orElse(null), dagCtx,
                    operationsService, client, vcsInfo, prNumber, unanalyzedCommits);

            Set<String> changedFiles = DiffParsingUtils.parseFilePathsFromDiff(rawDiff);
            augmentChangedFilesFromPr(changedFiles, project, prNumber);

            // ── Hybrid analysis: AI analysis for uncovered direct pushes ─────
            // Check if unanalyzed commits are covered by open PRs.
            // If NOT covered (direct push without PR), run full AI analysis
            // on the commit range diff, producing CodeAnalysisIssues marked
            // with DetectionSource.DIRECT_PUSH_ANALYSIS.
            performDirectPushAnalysisIfNeeded(
                    project, request, unanalyzedCommits, rawDiff,
                    changedFiles, provider, consumer);

            EventNotificationEmitter.emitStatus(consumer, "analyzing_files", "Analyzing " + changedFiles.size() + " changed files");

            // ── Delegate to domain services ──────────────────────────────────
            Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                    vcsInfo, request.getCommitHash(), changedFiles);
            log.info("Branch archive: {} files extracted for {} changed files",
                    archiveContents.size(), changedFiles.size());

            Set<String> existingFiles = branchFileOperationsService.updateBranchFiles(
                    changedFiles, project, request.getTargetBranchName(), archiveContents);

            Branch branch = branchFileOperationsService.createOrUpdateProjectBranch(
                    project, request, existingBranchOpt.orElse(null));

            branchIssueMappingService.mapCodeAnalysisIssuesToBranch(
                    changedFiles, existingFiles, branch, project);

            branchIssueReconciliationService.reconcileIssueLineNumbers(
                    rawDiff, changedFiles, branch);

            // Update branch issue counts after mapping
            Branch refreshedBranch = refreshAndSaveIssueCounts(branch);
            log.info("Updated branch issue counts after mapping: total={}, high={}, medium={}, low={}, resolved={}",
                    refreshedBranch.getTotalIssues(), refreshedBranch.getHighSeverityCount(),
                    refreshedBranch.getMediumSeverityCount(), refreshedBranch.getLowSeverityCount(),
                    refreshedBranch.getResolvedCount());

            branchIssueReconciliationService.reanalyzeCandidateIssues(
                    changedFiles, existingFiles, refreshedBranch, project,
                    request, consumer, archiveContents);

            branchFileOperationsService.updateFileSnapshotsForBranch(
                    existingFiles, project, request, archiveContents);

            Branch branchForVerify = branchRepository.findByProjectIdAndBranchName(
                    project.getId(), request.getTargetBranchName()).orElse(refreshedBranch);
            branchIssueReconciliationService.verifyIssueLineNumbersWithSnippets(
                    changedFiles, project, branchForVerify);

            // ── Post-analysis housekeeping ────────────────────────────────────
            performIncrementalRagUpdate(request, project, vcsInfo, rawDiff, consumer);
            markBranchHealthy(project, request);
            markDagCommitsAnalyzed(project, unanalyzedCommits, request.getTargetBranchName());

            log.info("Reconciliation finished (Branch: {}, Commit: {}, status: HEALTHY)",
                    request.getTargetBranchName(), request.getCommitHash());

            return Map.of("status", "accepted", "cached", false,
                    "branch", request.getTargetBranchName());

        } catch (Exception e) {
            handleProcessFailure(project, request, unanalyzedCommits, e);
            throw e;
        } finally {
            analysisLockService.releaseLock(lockKey.get());
        }
    }

    // ═══════════════════ Full reconciliation (manual UI trigger) ═════════════

    /**
     * Perform a full reconciliation of ALL unresolved branch issues.
     * <p>
     * Unlike the normal branch analysis flow (which only processes files that
     * changed in the latest diff), this method collects ALL unresolved
     * BranchIssues, updates snapshots, and runs the full deterministic + AI
     * reconciliation pipeline.
     *
     * @param projectId  the project ID
     * @param branchName the branch name to reconcile
     * @param consumer   SSE event consumer for progress updates
     * @return summary map with reconciliation results
     */
    public Map<String, Object> fullReconcile(Long projectId, String branchName,
                                              Consumer<Map<String, Object>> consumer) throws IOException {
        Project project = projectService.getProjectWithConnections(projectId);

        Optional<Branch> branchOpt = branchRepository.findByProjectIdAndBranchName(projectId, branchName);
        if (branchOpt.isEmpty()) {
            throw new IllegalArgumentException("Branch not found: " + branchName);
        }
        Branch branch = branchOpt.get();

        String commitHash = resolveCommitHash(branch);

        Optional<String> lockKey = analysisLockService.acquireLockWithWait(
                project, branchName, AnalysisLockType.BRANCH_ANALYSIS, commitHash, null, consumer);
        if (lockKey.isEmpty()) {
            throw new AnalysisLockedException(
                    AnalysisLockType.BRANCH_ANALYSIS.name(), branchName, projectId);
        }

        try {
            EventNotificationEmitter.emitStatus(consumer, "started", "Full reconciliation started for branch: " + branchName);
            // 1. Collect ALL unresolved BranchIssues
            List<BranchIssue> allUnresolved = branchRepository.findByIdWithIssues(branch.getId())
                    .map(b -> b.getIssues().stream().filter(bi -> !bi.isResolved()).toList())
                    .orElse(Collections.emptyList());

            if (allUnresolved.isEmpty()) {
                EventNotificationEmitter.emitStatus(consumer, "completed", "No unresolved issues to reconcile");
                return Map.of("status", "completed", "branch", branchName,
                        "totalIssues", 0, "message", "No unresolved issues to reconcile");
            }

            // 2. Extract all unique file paths
            Set<String> allFilePaths = allUnresolved.stream()
                    .map(BranchIssue::getFilePath)
                    .filter(fp -> fp != null && !fp.isBlank())
                    .collect(Collectors.toSet());

            log.info("Full reconciliation: {} unresolved issues across {} files (branch={})",
                    allUnresolved.size(), allFilePaths.size(), branchName);

            EventNotificationEmitter.emitStatus(consumer, "checking_files",
                    "Checking " + allFilePaths.size() + " files with "
                            + allUnresolved.size() + " unresolved issues");

            // 3. Download branch archive ONCE for all file operations
            VcsInfo vcsInfo = getVcsInfo(project);
            Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                    vcsInfo, commitHash, allFilePaths);
            log.info("Full reconciliation archive: {} files extracted for {} requested",
                    archiveContents.size(), allFilePaths.size());

            // 4. Check file existence and update branch files
            Set<String> filesExistingInBranch = branchFileOperationsService.updateBranchFiles(
                    allFilePaths, project, branchName, archiveContents);

            EventNotificationEmitter.emitStatus(consumer, "updating_snapshots",
                    "Updating file content snapshots for " + filesExistingInBranch.size() + " files");        

            // Build a synthetic request for reuse by existing service methods
            BranchProcessRequest syntheticRequest = buildSyntheticRequest(projectId, branchName, commitHash);

            branchFileOperationsService.updateFileSnapshotsForBranch(
                    filesExistingInBranch, project, syntheticRequest, archiveContents);

            // 5. Run the full deterministic + AI reconciliation on ALL files
            EventNotificationEmitter.emitStatus(consumer, "reanalyzing_issues",
                    "Reanalyzing " + allUnresolved.size() + " issues across "
                            + allFilePaths.size() + " files");

            branchIssueReconciliationService.reanalyzeCandidateIssues(
                    allFilePaths, filesExistingInBranch, branch, project,
                    syntheticRequest, consumer, archiveContents);

            // 6. Snippet-based line verification
            Branch branchForVerify = branchRepository.findByProjectIdAndBranchName(projectId, branchName)
                    .orElse(branch);
            branchIssueReconciliationService.verifyIssueLineNumbersWithSnippets(
                    allFilePaths, project, branchForVerify);

            // 7. Refresh final counts
            Branch finalBranch = refreshAndSaveIssueCounts(branch);
            long resolvedAfter = finalBranch.getResolvedCount();
            long totalAfter = finalBranch.getTotalIssues();

            log.info("Full reconciliation complete: branch={}, total={}, resolved={}",
                    branchName, totalAfter, resolvedAfter);

            EventNotificationEmitter.emitStatus(consumer, "completed",
                    "Full reconciliation complete. " + totalAfter
                            + " total issues, " + resolvedAfter + " resolved.");

            return Map.of("status", "completed", "branch", branchName,
                    "totalIssues", totalAfter, "resolvedIssues", resolvedAfter,
                    "filesChecked", allFilePaths.size());
        } catch (Exception e) {
            log.error("Full reconciliation failed for branch {}: {}", branchName, e.getMessage(), e);
            EventNotificationEmitter.emitStatus(consumer, "error",
                    "Full reconciliation failed: " + e.getMessage());
            throw e;
        } finally {
            analysisLockService.releaseLock(lockKey.get());
        }
    }

    // ════════════════════════ Private orchestration helpers ══════════════════

    // ── Cache check ─────────────────────────────────────────────────────────

    /**
     * Check if the incoming commit was already SUCCESSFULLY analyzed.
     * Uses lastSuccessfulCommitHash so that failed attempts are re-processed.
     */
    private boolean matchCache(BranchProcessRequest request, Optional<Branch> existingBranchOpt,
                               Project project, Consumer<Map<String, Object>> consumer) {
        if (request.getCommitHash() == null || existingBranchOpt.isEmpty()) return false;

        String lastSuccess = existingBranchOpt.get().getLastSuccessfulCommitHash();
        if (!request.getCommitHash().equals(lastSuccess)) return false;

        log.info("Skipping branch analysis - commit {} already successfully analyzed for branch {} (project={})",
                request.getCommitHash(), request.getTargetBranchName(), project.getId());

        // Refresh file snapshots even on skip path
        try {
            VcsInfo vcsInfo = getVcsInfo(project);
            Set<String> branchFiles = branchFileOperationsService.getBranchFilePaths(
                    project.getId(), request.getTargetBranchName());

            if (!branchFiles.isEmpty()) {
                Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                        vcsInfo, request.getCommitHash(), branchFiles);
                branchFileOperationsService.updateFileSnapshotsForBranch(
                        branchFiles, project, request, archiveContents);
            }
        } catch (Exception snapEx) {
            log.warn("Failed to refresh file snapshots on skip path (non-critical): {}",
                    snapEx.getMessage());
        }

        EventNotificationEmitter.emitStatus(consumer, "skipped",
                "Commit already successfully analyzed for this branch");
        return true;
    }

    // ── DAG sync ────────────────────────────────────────────────────────────

    /** Intermediate context holder for DAG walk results. */
    private record DagContext(List<String> unanalyzedCommits, String diffBase, boolean skipAnalysis) {}

    /**
     * Sync the git graph for the branch and determine unanalyzed commits
     * and the diff base commit.
     */
    private DagContext syncDag(Project project, VcsInfo vcsInfo, BranchProcessRequest request) {
        List<String> unanalyzedCommits = Collections.emptyList();
        String dagDiffBase = null;

        try {
            Map<String, CommitNode> nodeMap = gitGraphSyncService.syncBranchGraph(
                    project, vcsClientProvider.getClient(vcsInfo.vcsConnection()),
                    request.getTargetBranchName(), 100);

            if (!nodeMap.isEmpty() && request.getCommitHash() != null) {
                List<String> dagResult = gitGraphSyncService.findUnanalyzedCommitRange(
                        nodeMap, request.getCommitHash());

                if (dagResult == null) {
                    log.info("DAG could not resolve HEAD commit {} — falling back to legacy analysis",
                            request.getCommitHash());
                    return new DagContext(Collections.emptyList(), null, false);
                } else if (dagResult.isEmpty()) {
                    log.info("DAG shows HEAD commit {} is already analyzed — skipping branch analysis",
                            request.getCommitHash());
                    return new DagContext(Collections.emptyList(), null, true);
                } else {
                    unanalyzedCommits = dagResult;
                }

                dagDiffBase = gitGraphSyncService.findAnalyzedAncestor(
                        nodeMap, request.getCommitHash());

                log.info("DAG analysis: {} unanalyzed commits, diff base = {} (branch={})",
                        unanalyzedCommits.size(),
                        dagDiffBase != null
                                ? dagDiffBase.substring(0, Math.min(7, dagDiffBase.length()))
                                : "none (first analysis)",
                        request.getTargetBranchName());
            }
        } catch (Exception e) {
            log.warn("Git graph sync/walk failed for branch {} — falling back to legacy diff strategy: {}",
                    request.getTargetBranchName(), e.getMessage());
        }

        return new DagContext(unanalyzedCommits, dagDiffBase, false);
    }

    // ── PR number resolution ────────────────────────────────────────────────

    /**
     * If sourcePrNumber is not set, try to look it up from the commit.
     * This handles cases where branch analysis is triggered by push events.
     */
    private Long resolvePrNumber(BranchProcessRequest request,
                                 VcsOperationsService operationsService,
                                 OkHttpClient client, VcsInfo vcsInfo) {
        Long prNumber = request.getSourcePrNumber();
        if (prNumber == null && request.getCommitHash() != null) {
            try {
                prNumber = operationsService.findPullRequestForCommit(
                        client, vcsInfo.workspace(), vcsInfo.repoSlug(), request.getCommitHash());
                if (prNumber != null) {
                    log.info("Found PR #{} for commit {} via API lookup", prNumber, request.getCommitHash());
                    request.sourcePrNumber = prNumber;
                }
            } catch (Exception e) {
                log.debug("Could not look up PR for commit {}: {}",
                        request.getCommitHash(), e.getMessage());
            }
        }
        return prNumber;
    }

    // ── Multi-tier diff fetching ────────────────────────────────────────────

    /**
     * 3-tier diff strategy (enhanced by DAG):
     * <ol>
     *   <li>Tier 0: DAG-derived diff base (nearest analyzed ancestor)</li>
     *   <li>Tier 1: Legacy delta diff (lastSuccessfulCommitHash..HEAD)</li>
     *   <li>Tier 1.5: Aggregate individual commit diffs</li>
     *   <li>Tier 2: PR diff</li>
     *   <li>Tier 3: Single commit diff</li>
     * </ol>
     */
    private String fetchDiff(BranchProcessRequest request, Branch existingBranch,
                             DagContext dagCtx, VcsOperationsService operationsService,
                             OkHttpClient client, VcsInfo vcsInfo,
                             Long prNumber, List<String> unanalyzedCommits) throws IOException {

        String lastSuccessfulCommit = existingBranch != null
                ? existingBranch.getLastSuccessfulCommitHash() : null;
        String rawDiff = null;

        // Tier 0: DAG-derived diff base (preferred)
        rawDiff = tryDagDiff(dagCtx, request, operationsService, client, vcsInfo, unanalyzedCommits);

        // Tier 1: Legacy delta diff
        if (rawDiff == null) {
            rawDiff = tryDeltaDiff(lastSuccessfulCommit, request, operationsService, client, vcsInfo);
        }

        // Tier 1.5: Aggregate individual commit diffs when range diff failed.
        // Handles cases where the base commit is a second-parent commit (e.g. from
        // a merged feature branch) and the range diff API returns empty.
        if (rawDiff == null && !unanalyzedCommits.isEmpty()) {
            rawDiff = tryAggregatedCommitDiffs(unanalyzedCommits, operationsService, client, vcsInfo);
        }

        // Tier 2: PR diff
        if (rawDiff == null && prNumber != null) {
            rawDiff = operationsService.getPullRequestDiff(
                    client, vcsInfo.workspace(), vcsInfo.repoSlug(), String.valueOf(prNumber));
            log.info("Fetched PR #{} diff for branch analysis (first analysis or delta fallback)", prNumber);
        }

        // Tier 3: Single commit diff (last resort)
        if (rawDiff == null) {
            rawDiff = operationsService.getCommitDiff(
                    client, vcsInfo.workspace(), vcsInfo.repoSlug(), request.getCommitHash());
            log.info("Fetched commit {} diff for branch analysis (first analysis, no delta or PR context)",
                    request.getCommitHash());
        }

        return rawDiff;
    }

    private String tryDagDiff(DagContext dagCtx, BranchProcessRequest request,
                              VcsOperationsService operationsService, OkHttpClient client,
                              VcsInfo vcsInfo, List<String> unanalyzedCommits) {
        if (dagCtx.diffBase == null || request.getCommitHash() == null
                || dagCtx.diffBase.equals(request.getCommitHash())) {
            return null;
        }
        try {
            String diff = operationsService.getCommitRangeDiff(
                    client, vcsInfo.workspace(), vcsInfo.repoSlug(),
                    dagCtx.diffBase, request.getCommitHash());
            if (diff != null && diff.isBlank()) {
                log.info("DAG-based diff ({}..{}) returned empty (likely merge commit) — falling through",
                        shortHash(dagCtx.diffBase), shortHash(request.getCommitHash()));
                return null;
            }
            log.info("Fetched DAG-based diff ({}..{}) — covers {} unanalyzed commits",
                    shortHash(dagCtx.diffBase), shortHash(request.getCommitHash()),
                    unanalyzedCommits.size());
            return diff;
        } catch (IOException e) {
            log.warn("DAG-based diff failed (ancestor {} may be unreachable), falling back: {}",
                    shortHash(dagCtx.diffBase), e.getMessage());
            return null;
        }
    }

    private String tryDeltaDiff(String lastSuccessfulCommit, BranchProcessRequest request,
                                VcsOperationsService operationsService, OkHttpClient client,
                                VcsInfo vcsInfo) {
        if (lastSuccessfulCommit == null || lastSuccessfulCommit.equals(request.getCommitHash())) {
            return null;
        }
        try {
            String diff = operationsService.getCommitRangeDiff(
                    client, vcsInfo.workspace(), vcsInfo.repoSlug(),
                    lastSuccessfulCommit, request.getCommitHash());
            if (diff != null && diff.isBlank()) {
                log.info("Delta diff ({}..{}) returned empty — falling through to next tier",
                        shortHash(lastSuccessfulCommit), shortHash(request.getCommitHash()));
                return null;
            }
            log.info("Fetched delta diff ({}..{}) for branch analysis — captures all changes since last success",
                    shortHash(lastSuccessfulCommit), shortHash(request.getCommitHash()));
            return diff;
        } catch (IOException e) {
            log.warn("Delta diff failed (base commit {} may no longer exist), falling back: {}",
                    shortHash(lastSuccessfulCommit), e.getMessage());
            return null;
        }
    }

    private String tryAggregatedCommitDiffs(List<String> unanalyzedCommits,
                                            VcsOperationsService operationsService,
                                            OkHttpClient client, VcsInfo vcsInfo) {
        int maxCommits = Math.min(unanalyzedCommits.size(), 50);
        log.info("Range diff unavailable — aggregating individual diffs for {} of {} unanalyzed commits",
                maxCommits, unanalyzedCommits.size());

        StringBuilder aggregatedDiff = new StringBuilder();
        int fetchedCount = 0;

        for (int i = 0; i < maxCommits; i++) {
            String hash = unanalyzedCommits.get(i);
            try {
                String commitDiff = operationsService.getCommitDiff(
                        client, vcsInfo.workspace(), vcsInfo.repoSlug(), hash);
                if (commitDiff != null && !commitDiff.isBlank()) {
                    aggregatedDiff.append(commitDiff);
                    if (!commitDiff.endsWith("\n")) {
                        aggregatedDiff.append("\n");
                    }
                    fetchedCount++;
                }
            } catch (Exception e) {
                log.warn("Failed to fetch diff for commit {} (skipping): {}",
                        shortHash(hash), e.getMessage());
            }
        }

        if (fetchedCount > 0) {
            String result = aggregatedDiff.toString();
            log.info("Aggregated {} individual commit diffs ({} chars) as fallback for empty range diff",
                    fetchedCount, result.length());
            return result;
        }
        return null;
    }

    // ── PR issue augmentation ───────────────────────────────────────────────

    /**
     * When branch analysis is triggered by a PR merge, augment the changed-files
     * set with file paths from the merged PR's analysis.  This ensures
     * mapCodeAnalysisIssuesToBranch picks up issues that the diff didn't cover
     * (e.g. fast-forward merges, condensed diffs).
     */
    private void augmentChangedFilesFromPr(Set<String> changedFiles, Project project, Long prNumber) {
        if (prNumber == null) return;
        try {
            Set<String> prFilePaths = branchIssueMappingService.findPrIssuePaths(
                    project.getId(), prNumber);
            int added = 0;
            for (String fp : prFilePaths) {
                if (changedFiles.add(fp)) added++;
            }
            if (added > 0) {
                log.info("Augmented changedFiles with {} additional file paths from merged PR #{} (total now: {})",
                        added, prNumber, changedFiles.size());
            }
        } catch (Exception e) {
            log.warn("Failed to augment changedFiles from merged PR #{} (non-critical): {}",
                    prNumber, e.getMessage());
        }
    }

    // ── Hybrid analysis: direct push AI analysis ──────────────────────────

    /**
     * Performs AI analysis on uncovered direct pushes (commits not in any open PR).
     * <p>
     * This is the core of the hybrid branch analysis flow:
     * <ol>
     *   <li>Check if unanalyzed commits are covered by open PRs targeting this branch</li>
     *   <li>If fully covered → skip (PR analysis will handle them)</li>
     *   <li>If not covered or partially covered → build AI request from commit range diff,
     *       call inference orchestrator, save resulting CodeAnalysis with
     *       {@code DetectionSource.DIRECT_PUSH_ANALYSIS}, then map issues to branch</li>
     * </ol>
     *
     * @param project            the project
     * @param request            the branch analysis request
     * @param unanalyzedCommits  commits not yet analyzed (from DAG)
     * @param rawDiff            the commit range diff
     * @param changedFiles       files changed in the diff
     * @param provider           the VCS provider type
     * @param consumer           SSE event consumer
     */
    private void performDirectPushAnalysisIfNeeded(
            Project project,
            BranchProcessRequest request,
            List<String> unanalyzedCommits,
            String rawDiff,
            Set<String> changedFiles,
            EVcsProvider provider,
            Consumer<Map<String, Object>> consumer) {

        if (unanalyzedCommits.isEmpty()) {
            log.debug("No unanalyzed commits — skipping direct push analysis check");
            return;
        }

        if (rawDiff == null || rawDiff.isBlank()) {
            log.debug("No diff available — skipping direct push analysis");
            return;
        }

        // Check if a PR analysis lock is active for this branch.
        // If so, wait — the PR analysis will handle these commits.
        boolean prAnalysisInProgress = analysisLockService.isLocked(
                project.getId(), request.getTargetBranchName(), AnalysisLockType.PR_ANALYSIS);
        if (prAnalysisInProgress) {
            log.info("PR analysis in progress for branch {} — skipping direct push analysis (PR will cover it)",
                    request.getTargetBranchName());
            return;
        }

        // Check commit coverage by open PRs
        CommitCoverageService.CoverageResult coverage = commitCoverageService.checkCoverage(
                project.getId(), request.getTargetBranchName(), unanalyzedCommits);

        switch (coverage.status()) {
            case FULLY_COVERED:
                log.info("All {} unanalyzed commits are covered by open PRs — skipping direct push analysis",
                        unanalyzedCommits.size());
                return;
            case PARTIALLY_COVERED:
                log.info("{} of {} unanalyzed commits not covered by open PRs — running direct push analysis",
                        coverage.uncoveredCommits().size(), unanalyzedCommits.size());
                break;
            case NOT_COVERED:
                log.info("None of {} unanalyzed commits are covered by open PRs — running direct push analysis",
                        unanalyzedCommits.size());
                break;
        }

        EventNotificationEmitter.emitStatus(consumer, "direct_push_analysis",
                "Analyzing " + coverage.uncoveredCommits().size()
                        + " uncovered direct push commits via AI");

        try {
            // Build AI analysis request using the VCS-specific service
            VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);
            Map<String, String> fileContents = Collections.emptyMap();

            // Try to extract file contents from the archive for better line-hash computation
            try {
                VcsInfo vcsInfo = getVcsInfo(project);
                Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                        vcsInfo, request.getCommitHash(), changedFiles);
                if (!archiveContents.isEmpty()) {
                    fileContents = archiveContents;
                }
            } catch (Exception e) {
                log.warn("Failed to download archive for direct push analysis file contents (non-critical): {}",
                        e.getMessage());
            }

            AiAnalysisRequest aiRequest = aiClientService.buildDirectPushAnalysisRequest(
                    project, request, rawDiff, fileContents, new ArrayList<>(changedFiles));

            // Call the inference orchestrator
            Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiRequest, event -> {
                try {
                    consumer.accept(event);
                } catch (Exception ex) {
                    log.debug("Event consumer failed during direct push analysis: {}", ex.getMessage());
                }
            });

            // Save the analysis with DetectionSource.DIRECT_PUSH_ANALYSIS
            CodeAnalysis directPushAnalysis = codeAnalysisService.createDirectPushAnalysisFromAiResponse(
                    project, aiResponse, request.getTargetBranchName(),
                    request.getCommitHash(), fileContents);

            int issuesFound = directPushAnalysis.getIssues() != null
                    ? directPushAnalysis.getIssues().size() : 0;

            log.info("Direct push analysis completed: project={}, branch={}, commit={}, {} issues found",
                    project.getId(), request.getTargetBranchName(),
                    request.getCommitHash(), issuesFound);

            EventNotificationEmitter.emitStatus(consumer, "direct_push_analysis_complete",
                    "Direct push analysis found " + issuesFound + " issues");

        } catch (Exception e) {
            // Direct push analysis failure is non-fatal — reconciliation will still run
            log.warn("Direct push analysis failed (non-fatal, reconciliation will still run): {}",
                    e.getMessage(), e);
            EventNotificationEmitter.emitStatus(consumer, "direct_push_analysis_failed",
                    "Direct push analysis failed (non-critical): " + e.getMessage());
        }
    }

    // ── Branch health management ────────────────────────────────────────────

    private void markBranchHealthy(Project project, BranchProcessRequest request) {
        branchRepository.findByProjectIdAndBranchName(project.getId(), request.getTargetBranchName())
                .ifPresent(b -> {
                    b.markHealthy(request.getCommitHash());
                    branchRepository.save(b);
                });
    }

    private void markDagCommitsAnalyzed(Project project, List<String> unanalyzedCommits,
                                         String branchName) {
        if (unanalyzedCommits.isEmpty()) return;
        try {
            gitGraphSyncService.markCommitsAnalyzed(project.getId(), unanalyzedCommits);
            log.info("DAG: Marked {} commits as ANALYZED after successful branch analysis (branch={})",
                    unanalyzedCommits.size(), branchName);
        } catch (Exception e) {
            log.warn("Failed to mark commits as ANALYZED in DAG (non-critical): {}", e.getMessage());
        }
    }

    private void handleProcessFailure(Project project, BranchProcessRequest request,
                                      List<String> unanalyzedCommits, Exception e) {
        try {
            branchRepository.findByProjectIdAndBranchName(project.getId(), request.getTargetBranchName())
                    .ifPresent(b -> {
                        b.markStale();
                        branchRepository.save(b);
                        log.info("Marked branch {} as STALE (consecutiveFailures={})",
                                request.getTargetBranchName(), b.getConsecutiveFailures());
                    });
        } catch (Exception staleEx) {
            log.warn("Failed to mark branch as STALE: {}", staleEx.getMessage());
        }

        if (!unanalyzedCommits.isEmpty()) {
            try {
                gitGraphSyncService.markCommitsFailed(project.getId(), unanalyzedCommits);
                log.info("DAG: Marked {} commits as FAILED after branch analysis failure (branch={})",
                        unanalyzedCommits.size(), request.getTargetBranchName());
            } catch (Exception dagEx) {
                log.warn("Failed to mark commits as FAILED in DAG: {}", dagEx.getMessage());
            }
        }

        log.warn("Branch reconciliation failed (Branch: {}, Commit: {}): {}",
                request.getTargetBranchName(), request.getCommitHash(), e.getMessage());
    }

    // ── RAG incremental update ──────────────────────────────────────────────

    private void performIncrementalRagUpdate(BranchProcessRequest request, Project project,
                                             VcsInfo vcsInfo, String commitDiff,
                                             Consumer<Map<String, Object>> consumer) {
        if (ragOperationsService == null) {
            log.info("Skipping RAG incremental update - RagOperationsService not available");
            return;
        }
        try {
            if (!ragOperationsService.isRagEnabled(project)) {
                log.info("Skipping RAG incremental update - RAG not enabled for project={}", project.getId());
                return;
            }
            if (!ragOperationsService.isRagIndexReady(project)) {
                log.info("Skipping RAG incremental update - RAG index not yet ready for project={}", project.getId());
                return;
            }

            String targetBranch = request.getTargetBranchName();
            String baseBranch = ragOperationsService.getBaseBranch(project);

            if (targetBranch.equals(baseBranch)) {
                log.info("Main branch push - updating RAG index for project={}, branch={}, commit={}",
                        project.getId(), targetBranch, request.getCommitHash());
                EventNotificationEmitter.emitStatus(consumer, "rag_update",
                        "Updating RAG index with changed files for main branch push");
                ragOperationsService.triggerIncrementalUpdate(
                        project, targetBranch, request.getCommitHash(), commitDiff, consumer);
            } else {
                log.info("Non-main branch push - updating branch index for project={}, branch={}",
                        project.getId(), targetBranch);
                ragOperationsService.updateBranchIndex(project, targetBranch, consumer);
            }

            log.info("RAG update completed for project={}, branch={}, commit={}",
                    project.getId(), targetBranch, request.getCommitHash());
        } catch (Exception e) {
            log.warn("RAG incremental update failed (non-critical): {}", e.getMessage());
            EventNotificationEmitter.emitStatus(consumer, "rag_update_failed",
                    "RAG incremental update failed (non-critical): " + e.getMessage());
        }
    }

    // ── Shared small helpers ────────────────────────────────────────────────

    /** Refresh a branch entity from DB and update issue counts. */
    private Branch refreshAndSaveIssueCounts(Branch branch) {
        Branch refreshed = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
        refreshed.updateIssueCounts();
        branchRepository.save(refreshed);
        return refreshed;
    }

    /** Resolve commit hash from a branch entity, preferring lastSuccessfulCommitHash. */
    private String resolveCommitHash(Branch branch) {
        String hash = branch.getLastSuccessfulCommitHash();
        if (hash == null || hash.isBlank()) {
            hash = branch.getCommitHash();
        }
        if (hash == null || hash.isBlank()) {
            throw new IllegalStateException("Branch has no commit hash — it may not have been analyzed yet");
        }
        return hash;
    }

    /** Build a synthetic BranchProcessRequest for fullReconcile. */
    private BranchProcessRequest buildSyntheticRequest(Long projectId, String branchName, String commitHash) {
        BranchProcessRequest req = new BranchProcessRequest();
        req.projectId = projectId;
        req.targetBranchName = branchName;
        req.commitHash = commitHash;
        req.analysisType = AnalysisType.BRANCH_ANALYSIS;
        return req;
    }

    /** Truncate a commit hash to 7 chars for log readability. */
    private static String shortHash(String hash) {
        return hash != null ? hash.substring(0, Math.min(7, hash.length())) : "null";
    }
}
