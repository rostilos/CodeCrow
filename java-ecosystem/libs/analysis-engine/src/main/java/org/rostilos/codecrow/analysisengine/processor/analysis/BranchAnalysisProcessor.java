package org.rostilos.codecrow.analysisengine.processor.analysis;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.commitgraph.dag.CommitRangeContext;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.commitgraph.service.BranchCommitService;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.branch.*;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.util.DiffParsingUtils;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectValidationService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.commitgraph.service.CommitCoverageService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.events.EventNotificationEmitter;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;
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
 * <li>{@link BranchFileOperationsService} — file-level ops (archive, snapshots,
 * branch files)</li>
 * <li>{@link BranchIssueMappingService} — CAI → BranchIssue mapping with
 * dedup</li>
 * <li>{@link BranchIssueReconciliationService} — tracking, reconciliation, AI
 * fallback</li>
 * <li>{@link DiffParsingUtils} — stateless diff parsing utilities</li>
 * </ul>
 */
@Service
public class BranchAnalysisProcessor {

        private static final Logger log = LoggerFactory.getLogger(BranchAnalysisProcessor.class);

        // ── Core dependencies ───────────────────────────────────────────────────
        private final ProjectValidationService projectService;
        private final BranchRepository branchRepository;
        private final VcsClientProvider vcsClientProvider;
        private final VcsServiceFactory vcsServiceFactory;
        private final AnalysisLockService analysisLockService;

        // ── Branch domain services ───────────────────────────────────────────
        private final BranchFileOperationsService branchFileOperationsService;
        private final BranchIssueMappingService branchIssueMappingService;
        private final BranchIssueReconciliationService branchIssueReconciliationService;
        private final BranchHealthService branchHealthService;
        private final BranchDiffFetcher branchDiffFetcher;

        // ── Commit tracking services ────────────────────────────────────────
        private final BranchCommitService branchCommitService;
        private final AnalyzedCommitService analyzedCommitService;

        // ── Hybrid (direct push) analysis dependencies ──────────────────────────
        private final CommitCoverageService commitCoverageService;
        private final CodeAnalysisService codeAnalysisService;
        private final AiAnalysisClient aiAnalysisClient;
        private final PullRequestService pullRequestService;
        private final AstScopeEnricher astScopeEnricher;

        /**
         * Optional RAG operations service — can be null if RAG module is not deployed.
         */
        private final RagOperationsService ragOperationsService;

        public BranchAnalysisProcessor(
                        ProjectValidationService projectService,
                        BranchRepository branchRepository,
                        VcsClientProvider vcsClientProvider,
                        VcsServiceFactory vcsServiceFactory,
                        AnalysisLockService analysisLockService,
                        BranchFileOperationsService branchFileOperationsService,
                        BranchIssueMappingService branchIssueMappingService,
                        BranchIssueReconciliationService branchIssueReconciliationService,
                        BranchHealthService branchHealthService,
                        BranchDiffFetcher branchDiffFetcher,
                        BranchCommitService branchCommitService,
                        AnalyzedCommitService analyzedCommitService,
                        CommitCoverageService commitCoverageService,
                        CodeAnalysisService codeAnalysisService,
                        AiAnalysisClient aiAnalysisClient,
                        PullRequestService pullRequestService,
                        AstScopeEnricher astScopeEnricher,
                        @Autowired(required = false) RagOperationsService ragOperationsService) {
                this.projectService = projectService;
                this.branchRepository = branchRepository;
                this.vcsClientProvider = vcsClientProvider;
                this.vcsServiceFactory = vcsServiceFactory;
                this.analysisLockService = analysisLockService;
                this.branchFileOperationsService = branchFileOperationsService;
                this.branchIssueMappingService = branchIssueMappingService;
                this.branchIssueReconciliationService = branchIssueReconciliationService;
                this.branchHealthService = branchHealthService;
                this.branchDiffFetcher = branchDiffFetcher;
                this.branchCommitService = branchCommitService;
                this.analyzedCommitService = analyzedCommitService;
                this.commitCoverageService = commitCoverageService;
                this.codeAnalysisService = codeAnalysisService;
                this.aiAnalysisClient = aiAnalysisClient;
                this.pullRequestService = pullRequestService;
                this.astScopeEnricher = astScopeEnricher;
                this.ragOperationsService = ragOperationsService;
        }

        /**
         * Primary entry point — run branch analysis for a given request.
         *
         * @param request  the branch analysis request
         * @param consumer SSE event consumer for progress updates
         * @return result map with status/metadata
         */
        public Map<String, Object> process(
                        BranchProcessRequest request,
                        Consumer<Map<String, Object>> consumer) throws IOException {
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

                        VcsRepoInfoImpl vcsRepoInfoImpl = ProjectVcsInfoRetriever.getVcsInfo(project);
                        OkHttpClient client = vcsClientProvider.getHttpClient(vcsRepoInfoImpl.vcsConnection());
                        EVcsProvider provider = ProjectVcsInfoRetriever.getVcsProvider(project);
                        VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);

                        // ── Commit range resolution ───────────────────────────────────
                        CommitRangeContext rangeCtx = branchCommitService.resolveCommitRange(project,
                                vcsRepoInfoImpl.vcsConnection(),
                                request.getTargetBranchName(),
                                request.getCommitHash());
                        unanalyzedCommits = rangeCtx.getUnanalyzedCommits();

                        if (rangeCtx.getSkipAnalysis()) {
                                // Important: We must create/update the branch record even if we skip full
                                // analysis!
                                // For fast-forward merges, the commits are analyzed, but the branch HEAD needs
                                // to advance
                                branchFileOperationsService.createOrUpdateProjectBranch(
                                                project, request, existingBranchOpt.orElse(null));

                                // Advance lastKnownHeadCommit even when skipping full analysis.
                                // Without this, the next analysis would diff from a stale base and
                                // pick up files from other PRs that were already analyzed.
                                branchRepository.findByProjectIdAndBranchName(
                                        project.getId(), request.getTargetBranchName())
                                        .ifPresent(b -> {
                                                b.setLastKnownHeadCommit(request.getCommitHash());
                                                branchRepository.save(b);
                                                log.info("Advanced lastKnownHeadCommit to {} on skip path (branch={})",
                                                        request.getCommitHash(), request.getTargetBranchName());
                                        });

                                EventNotificationEmitter.emitStatus(consumer, "skipped",
                                                "All commits already analyzed");
                                return Map.of("status", "skipped", "reason", "already_analyzed",
                                                "branch", request.getTargetBranchName(),
                                                "commitHash", request.getCommitHash());
                        }

                        EventNotificationEmitter.emitStatus(consumer, "fetching_diff", "Fetching diff for analysis");

                        // ── PR number resolution ─────────────────────────────────────────
                        Long prNumber = resolvePrNumber(request, operationsService, client, vcsRepoInfoImpl);

                        // ── Merge-commit detection (safety net for webhook race condition) ──
                        // If prNumber is still null, the PR number may have been lost because
                        // repo:push arrived before pullrequest:fulfilled. Detect merge commits
                        // by checking parent count: merge commits always have >1 parent.
                        boolean isMergeCommit = false;
                        if (prNumber == null && request.getCommitHash() != null) {
                                try {
                                        VcsClient vcsClient = vcsClientProvider.getClient(vcsRepoInfoImpl.vcsConnection());
                                        List<VcsCommit> headCommits = vcsClient.getCommitHistory(
                                                        vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                                                        request.getCommitHash(), 1);
                                        if (!headCommits.isEmpty()
                                                        && headCommits.get(0).parentHashes() != null
                                                        && headCommits.get(0).parentHashes().size() > 1) {
                                                isMergeCommit = true;
                                                log.info("Detected merge commit {} (parents: {}) — attempting PR lookup via parent",
                                                                request.getCommitHash().substring(0, Math.min(7, request.getCommitHash().length())),
                                                                headCommits.get(0).parentHashes().size());
                                                // Second parent of a merge commit is the source branch HEAD.
                                                // Try finding the PR via this parent, which is the commit the PR was based on.
                                                String sourceParent = headCommits.get(0).parentHashes().get(1);
                                                try {
                                                        prNumber = operationsService.findPullRequestForCommit(
                                                                        client, vcsRepoInfoImpl.workspace(),
                                                                        vcsRepoInfoImpl.repoSlug(), sourceParent);
                                                        if (prNumber != null) {
                                                                log.info("Found PR #{} from merge commit's second parent {}",
                                                                                prNumber, sourceParent.substring(0, Math.min(7, sourceParent.length())));
                                                                request.sourcePrNumber = prNumber;
                                                        }
                                                } catch (Exception e) {
                                                        log.debug("Could not find PR from merge parent {}: {}",
                                                                        sourceParent, e.getMessage());
                                                }
                                        }
                                } catch (Exception e) {
                                        log.debug("Could not detect merge commit (non-critical): {}", e.getMessage());
                                }
                        }

                        // Mark the source PR as MERGED if this branch analysis was triggered by a PR
                        // merge.
                        // This keeps PullRequestState accurate for commit coverage checks.
                        if (prNumber != null) {
                                try {
                                        pullRequestService.markPullRequestMerged(project.getId(), prNumber);
                                } catch (Exception e) {
                                        log.debug("Could not mark PR #{} as merged (may not exist yet): {}", prNumber,
                                                        e.getMessage());
                                }
                        }

                        // ── Multi-tier diff strategy ─────────────────────────────────────
                        String rawDiff = branchDiffFetcher.fetchDiff(request, existingBranchOpt.orElse(null), rangeCtx,
                                        operationsService, client, vcsRepoInfoImpl, prNumber, unanalyzedCommits);

                        Set<String> changedFiles = DiffParsingUtils.parseFilePathsFromDiff(rawDiff);

                        // Detect first-ever analysis for this branch (no prior successful commit)
                        boolean isFirstAnalysis = existingBranchOpt.isEmpty()
                                        || existingBranchOpt.get().getLastSuccessfulCommitHash() == null;

                        // ── Hybrid analysis: AI analysis for uncovered direct pushes ─────
                        // Skip on first analysis — the existing codebase predates CodeCrow
                        // and should not be treated as "uncovered direct pushes".
                        if (!isFirstAnalysis) {
                                performDirectPushAnalysisIfNeeded(
                                                project, request, unanalyzedCommits, rawDiff,
                                                changedFiles, provider, consumer, prNumber, isMergeCommit);
                        } else {
                                log.info("First analysis for branch {} — skipping direct push analysis (establishing baseline, {} files)",
                                                request.getTargetBranchName(), changedFiles.size());
                        }

                        EventNotificationEmitter.emitStatus(consumer, "analyzing_files",
                                        "Analyzing " + changedFiles.size() + " changed files");

                        Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                                        vcsRepoInfoImpl, request.getCommitHash(), changedFiles);
                        log.info("Branch archive: {} files extracted for {} changed files",
                                        archiveContents.size(), changedFiles.size());

                        Set<String> existingFiles = branchFileOperationsService.updateBranchFiles(
                                        changedFiles, project, request.getTargetBranchName(), archiveContents);

                        Branch branch = branchFileOperationsService.createOrUpdateProjectBranch(
                                        project, request, existingBranchOpt.orElse(null));

                        branchIssueMappingService.mapCodeAnalysisIssuesToBranch(changedFiles, existingFiles, branch,
                                        project);
                        branchIssueReconciliationService.reconcileIssueLineNumbers(rawDiff, changedFiles, branch);

                        // Update branch issue counts after mapping
                        Branch refreshedBranch = refreshAndSaveIssueCounts(branch);
                        log.info("Updated branch issue counts after mapping: total={}, high={}, medium={}, low={}, resolved={}",
                                        refreshedBranch.getTotalIssues(), refreshedBranch.getHighSeverityCount(),
                                        refreshedBranch.getMediumSeverityCount(), refreshedBranch.getLowSeverityCount(),
                                        refreshedBranch.getResolvedCount());

                        branchIssueReconciliationService.reanalyzeCandidateIssues(
                                        changedFiles, existingFiles, refreshedBranch, project,
                                        request, consumer, archiveContents, rawDiff);

                        // ── Deterministic sweep: catch stale issues in non-diff files ────
                        // The normal reconciliation above only checks files in the diff.
                        // The sweep checks ALL remaining unresolved issues that have reliable
                        // content anchors (codeSnippet/lineHash). Zero AI cost.
                        int sweptCount = branchIssueReconciliationService.sweepDeterministicResolutions(
                                        changedFiles, refreshedBranch, project, request, archiveContents);
                        if (sweptCount > 0) {
                                refreshedBranch = refreshAndSaveIssueCounts(refreshedBranch);
                        }

                        branchFileOperationsService.updateFileSnapshotsForBranch(existingFiles, project, request,
                                        archiveContents);

                        Branch branchForVerify = branchRepository.findByProjectIdAndBranchName(
                                        project.getId(), request.getTargetBranchName()).orElse(refreshedBranch);
                        branchIssueReconciliationService.verifyIssueLineNumbersWithSnippets(
                                        changedFiles, project, branchForVerify);

                        // ── Post-analysis housekeeping ────────────────────────────────────
                        performIncrementalRagUpdate(request, project, rawDiff, consumer);
                        branchHealthService.markBranchHealthy(project, request);
                        branchHealthService.recordCommitsAnalyzed(project, unanalyzedCommits,
                                        request.getTargetBranchName());

                        log.info("Reconciliation finished (Branch: {}, Commit: {}, status: HEALTHY)",
                                        request.getTargetBranchName(), request.getCommitHash());

                        return Map.of("status", "accepted", "cached", false,
                                        "branch", request.getTargetBranchName());

                } catch (Exception e) {
                        branchHealthService.handleProcessFailure(project, request, unanalyzedCommits, e);
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
                        EventNotificationEmitter.emitStatus(consumer, "started",
                                        "Full reconciliation started for branch: " + branchName);
                        // 1. Collect ALL unresolved BranchIssues
                        List<BranchIssue> allUnresolved = branchRepository.findByIdWithIssues(branch.getId())
                                        .map(b -> b.getIssues().stream().filter(bi -> !bi.isResolved()).toList())
                                        .orElse(Collections.emptyList());

                        if (allUnresolved.isEmpty()) {
                                EventNotificationEmitter.emitStatus(consumer, "completed",
                                                "No unresolved issues to reconcile");
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
                        VcsRepoInfoImpl vcsRepoInfoImpl = ProjectVcsInfoRetriever.getVcsInfo(project);
                        Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                                        vcsRepoInfoImpl, commitHash, allFilePaths);
                        log.info("Full reconciliation archive: {} files extracted for {} requested",
                                        archiveContents.size(), allFilePaths.size());

                        // 4. Check file existence and update branch files
                        Set<String> filesExistingInBranch = branchFileOperationsService.updateBranchFiles(
                                        allFilePaths, project, branchName, archiveContents);

                        EventNotificationEmitter.emitStatus(consumer, "updating_snapshots",
                                        "Updating file content snapshots for " + filesExistingInBranch.size()
                                                        + " files");

                        // Build a synthetic request for reuse by existing service methods
                        BranchProcessRequest syntheticRequest = buildSyntheticRequest(projectId, branchName,
                                        commitHash);

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

        /**
         * Check if the incoming commit was already SUCCESSFULLY analyzed.
         * Uses lastSuccessfulCommitHash so that failed attempts are re-processed.
         */
        private boolean matchCache(BranchProcessRequest request, Optional<Branch> existingBranchOpt,
                        Project project, Consumer<Map<String, Object>> consumer) {
                if (request.getCommitHash() == null || existingBranchOpt.isEmpty())
                        return false;

                String lastSuccess = existingBranchOpt.get().getLastSuccessfulCommitHash();
                if (!request.getCommitHash().equals(lastSuccess))
                        return false;

                log.info("Skipping branch analysis - commit {} already successfully analyzed for branch {} (project={})",
                                request.getCommitHash(), request.getTargetBranchName(), project.getId());

                // Refresh file snapshots even on skip path
                try {
                        VcsRepoInfoImpl vcsRepoInfoImpl = ProjectVcsInfoRetriever.getVcsInfo(project);
                        Set<String> branchFiles = branchFileOperationsService.getBranchFilePaths(
                                        project.getId(), request.getTargetBranchName());

                        if (!branchFiles.isEmpty()) {
                                Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                                                vcsRepoInfoImpl, request.getCommitHash(), branchFiles);
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

        /**
         * If sourcePrNumber is not set, try to look it up from the commit.
         * This handles cases where branch analysis is triggered by push events.
         */
        private Long resolvePrNumber(BranchProcessRequest request,
                        VcsOperationsService operationsService,
                        OkHttpClient client, VcsRepoInfoImpl vcsRepoInfoImpl) {
                Long prNumber = request.getSourcePrNumber();
                if (prNumber == null && request.getCommitHash() != null) {
                        try {
                                prNumber = operationsService.findPullRequestForCommit(
                                                client, vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                                                request.getCommitHash());
                                if (prNumber != null) {
                                        log.info("Found PR #{} for commit {} via API lookup", prNumber,
                                                        request.getCommitHash());
                                        request.sourcePrNumber = prNumber;
                                }
                        } catch (Exception e) {
                                log.debug("Could not look up PR for commit {}: {}",
                                                request.getCommitHash(), e.getMessage());
                        }
                }
                return prNumber;
        }

        /**
         * When branch analysis is triggered by a PR merge, augment the changed-files
         * set with file paths from the merged PR's analysis. This ensures
         * mapCodeAnalysisIssuesToBranch picks up issues that the diff didn't cover
         * (e.g. fast-forward merges, condensed diffs).
         */
        private void augmentChangedFilesFromPr(Set<String> changedFiles, Project project, Long prNumber) {
                if (prNumber == null)
                        return;
                try {
                        Set<String> prFilePaths = branchIssueMappingService.findPrIssuePaths(
                                        project.getId(), prNumber);
                        int added = 0;
                        for (String fp : prFilePaths) {
                                if (changedFiles.add(fp))
                                        added++;
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
         * <li>Check if unanalyzed commits are covered by open PRs targeting this
         * branch</li>
         * <li>If fully covered → skip (PR analysis will handle them)</li>
         * <li>If not covered or partially covered → build AI request from commit range
         * diff,
         * call inference orchestrator, save resulting CodeAnalysis with
         * {@code DetectionSource.DIRECT_PUSH_ANALYSIS}, then map issues to branch</li>
         * </ol>
         */
        private void performDirectPushAnalysisIfNeeded(
                        Project project,
                        BranchProcessRequest request,
                        List<String> unanalyzedCommits,
                        String rawDiff,
                        Set<String> changedFiles,
                        EVcsProvider provider,
                        Consumer<Map<String, Object>> consumer,
                        Long mergedPrNumber,
                        boolean isMergeCommit) {

                if (unanalyzedCommits.isEmpty()) {
                        log.debug("No unanalyzed commits — skipping direct push analysis check");
                        return;
                }

                if (rawDiff == null || rawDiff.isBlank()) {
                        log.debug("No diff available — skipping direct push analysis");
                        return;
                }

                // ── Fast path: PR merge ──────────────────────────────────────────
                // If this branch analysis was triggered by a PR merge, the code was
                // already reviewed during the PR. Always skip — a merge is never a
                // direct push, and any missing analysis will be handled by reconciliation.
                if (mergedPrNumber != null) {
                        log.info("Skipping direct push analysis — branch event originates from PR #{} merge (not a direct push)",
                                        mergedPrNumber);
                        return;
                }

                // ── Safety net: merge commit without PR number ───────────────────
                // If the HEAD commit is a merge commit (>1 parent) but the PR number
                // was lost due to webhook race conditions, still skip. A merge commit
                // is NEVER a direct push — it's always the result of a PR merge or
                // manual merge.
                if (isMergeCommit) {
                        log.info("Skipping direct push analysis — HEAD commit is a merge commit " +
                                "(detected via parent count) but PR number was not resolved. " +
                                "This is a PR merge, not a direct push.");
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

                // Check commit coverage by open/merged PRs
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

                        // Try to extract file contents from the archive for better line-hash
                        // computation
                        try {
                                VcsRepoInfoImpl vcsRepoInfoImpl = ProjectVcsInfoRetriever.getVcsInfo(project);
                                Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                                                vcsRepoInfoImpl, request.getCommitHash(), changedFiles);
                                if (!archiveContents.isEmpty()) {
                                        fileContents = archiveContents;
                                }
                        } catch (Exception e) {
                                log.warn("Failed to download archive for direct push analysis file contents (non-critical): {}",
                                                e.getMessage());
                        }

                        List<AiAnalysisRequest> aiRequests = aiClientService.buildDirectPushAnalysisRequests(
                                        project, request, rawDiff, fileContents, new ArrayList<>(changedFiles));

                        // Call the inference orchestrator using single request
                        Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiRequests.get(0), event -> {
                                try {
                                        consumer.accept(event);
                                } catch (Exception ex) {
                                        log.debug("Event consumer failed during direct push analysis: {}",
                                                        ex.getMessage());
                                }
                        });

                        // Save the analysis with DetectionSource.DIRECT_PUSH_ANALYSIS
                        CodeAnalysis directPushAnalysis = codeAnalysisService.createDirectPushAnalysisFromAiResponse(
                                        project, aiResponse, request.getTargetBranchName(),
                                        request.getCommitHash(), fileContents);

                        int issuesFound = directPushAnalysis.getIssues() != null
                                        ? directPushAnalysis.getIssues().size()
                                        : 0;

                        // === AST scope enrichment for direct push issues ===
                        try {
                                if (directPushAnalysis.getIssues() != null && !directPushAnalysis.getIssues().isEmpty()
                                                && !fileContents.isEmpty()) {
                                        astScopeEnricher.enrichWithAstScopes(
                                                        directPushAnalysis.getIssues(), fileContents);
                                }
                        } catch (Exception astEx) {
                                log.warn("AST scope enrichment failed for direct push (non-critical): {}",
                                                astEx.getMessage());
                        }

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

        // ── RAG incremental update ──────────────────────────────────────────────
        private void performIncrementalRagUpdate(BranchProcessRequest request, Project project, String commitDiff,
                        Consumer<Map<String, Object>> consumer) {
                if (ragOperationsService == null) {
                        log.info("Skipping RAG incremental update - RagOperationsService not available");
                        EventNotificationEmitter.emitStatus(consumer, "rag_skipped",
                                        "RAG module not deployed — skipping incremental update");
                        return;
                }
                try {
                        if (!ragOperationsService.isRagEnabled(project)) {
                                log.info("Skipping RAG incremental update - RAG not enabled for project={}",
                                                project.getId());
                                EventNotificationEmitter.emitStatus(consumer, "rag_skipped",
                                                "RAG not enabled for this project — skipping incremental update");
                                return;
                        }
                        if (!ragOperationsService.isRagIndexReady(project)) {
                                log.info("Skipping RAG incremental update - RAG index not yet ready for project={}",
                                                project.getId());
                                EventNotificationEmitter.emitStatus(consumer, "rag_skipped",
                                                "RAG index not yet ready (initial indexing may still be in progress) — skipping incremental update");
                                return;
                        }

                        String targetBranch = request.getTargetBranchName();
                        String baseBranch = ragOperationsService.getBaseBranch(project);

                        // Health check: verify RAG pipeline is reachable before starting
                        if (!ragOperationsService.isRagPipelineHealthy()) {
                                log.warn("RAG pipeline is not reachable — skipping incremental update for project={}",
                                                project.getId());
                                EventNotificationEmitter.emitStatus(consumer, "rag_skipped",
                                                "RAG pipeline not reachable — skipping incremental update");
                                return;
                        }

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
                        EventNotificationEmitter.emitStatus(consumer, "rag_update_complete",
                                        "RAG index updated successfully for branch: " + targetBranch);
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

        /**
         * Resolve commit hash from a branch entity, preferring
         * lastSuccessfulCommitHash.
         */
        private String resolveCommitHash(Branch branch) {
                String hash = branch.getLastSuccessfulCommitHash();
                if (hash == null || hash.isBlank()) {
                        hash = branch.getCommitHash();
                }
                if (hash == null || hash.isBlank()) {
                        throw new IllegalStateException(
                                        "Branch has no commit hash — it may not have been analyzed yet");
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
}
