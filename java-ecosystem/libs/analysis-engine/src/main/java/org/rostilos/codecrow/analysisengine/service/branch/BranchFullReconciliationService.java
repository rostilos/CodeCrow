package org.rostilos.codecrow.analysisengine.service.branch;

import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectValidationService;
import org.rostilos.codecrow.analysisengine.service.PullRequestStatusSyncService;
import org.rostilos.codecrow.analysisengine.util.AnalysisScopeFilter;
import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.events.EventNotificationEmitter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;
import java.util.stream.Collectors;

/**
 * Manual branch reconciliation service.
 */
@Service
public class BranchFullReconciliationService {

    private static final Logger log = LoggerFactory.getLogger(BranchFullReconciliationService.class);

    private final ProjectValidationService projectService;
    private final BranchRepository branchRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final PullRequestRepository pullRequestRepository;
    private final AnalysisLockService analysisLockService;
    private final BranchFileOperationsService branchFileOperationsService;
    private final BranchIssueMappingService branchIssueMappingService;
    private final BranchIssueReconciliationService branchIssueReconciliationService;
    private final PullRequestStatusSyncService pullRequestStatusSyncService;

    public BranchFullReconciliationService(
            ProjectValidationService projectService,
            BranchRepository branchRepository,
            BranchIssueRepository branchIssueRepository,
            PullRequestRepository pullRequestRepository,
            AnalysisLockService analysisLockService,
            BranchFileOperationsService branchFileOperationsService,
            BranchIssueMappingService branchIssueMappingService,
            BranchIssueReconciliationService branchIssueReconciliationService,
            PullRequestStatusSyncService pullRequestStatusSyncService) {
        this.projectService = projectService;
        this.branchRepository = branchRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.pullRequestRepository = pullRequestRepository;
        this.analysisLockService = analysisLockService;
        this.branchFileOperationsService = branchFileOperationsService;
        this.branchIssueMappingService = branchIssueMappingService;
        this.branchIssueReconciliationService = branchIssueReconciliationService;
        this.pullRequestStatusSyncService = pullRequestStatusSyncService;
    }

    /**
     * Perform a full reconciliation of ALL unresolved branch issues.
     */
    public Map<String, Object> fullReconcile(
            Long projectId,
            String branchName,
            Consumer<Map<String, Object>> consumer) throws IOException {
        Project project = projectService.getProjectWithConnections(projectId);

        Optional<Branch> branchOpt = branchRepository.findByProjectIdAndBranchName(projectId, branchName);
        if (branchOpt.isEmpty()) {
            throw new IllegalArgumentException("Branch not found: " + branchName);
        }
        String requestedCommit = resolveCommitHash(branchOpt.get());
        Optional<String> lockKey = analysisLockService.acquireLockWithWait(
                project, branchName, AnalysisLockType.BRANCH_ANALYSIS, requestedCommit, null, consumer);
        if (lockKey.isEmpty()) throw new AnalysisLockedException(AnalysisLockType.BRANCH_ANALYSIS.name(), branchName, projectId);

        try (AnalysisLockService.LockLease lease = analysisLockService.maintainLockLease(
                lockKey.get(), analysisLockService.getLeaseMinutes(AnalysisLockType.BRANCH_ANALYSIS))) {
            requireOwnership(lease);
            // Reload after waiting: another completed operation may have advanced the branch.
            Branch branch = branchRepository.findByProjectIdAndBranchName(projectId, branchName).orElseThrow();
            String commitHash = resolveCommitHash(branch);
            analysisLockService.withOwnedLocks(List.of(lockKey.get()), () -> {
                Branch current = branchRepository.findById(branch.getId()).orElseThrow();
                current.beginReconciliation(false);
                branchRepository.save(current);
                return null;
            });
            EventNotificationEmitter.emitStatus(consumer, "started", "Full reconciliation started for branch: " + branchName);
            var prStatusSyncResult = pullRequestStatusSyncService.syncOpenPullRequestStates(project, consumer);
            requireOwnership(lease);
            MergedPrPlan prPlan = prepareMergedPrIssues(project, branch);
            Branch before = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
            long resolvedBefore = before.getIssues().stream().filter(BranchIssue::isResolved).count();
            Set<String> allFilePaths = before.getIssues().stream().filter(issue -> !issue.isResolved())
                    .map(BranchIssue::getFilePath).filter(path -> path != null && !path.isBlank())
                    .collect(Collectors.toCollection(LinkedHashSet::new));
            prPlan.pathsByPr().values().forEach(allFilePaths::addAll);
            AnalysisScopeFilter.retainIncluded(allFilePaths, project);
            Set<String> evidencePaths = branchIssueReconciliationService.findReconciliationPaths(branch, allFilePaths);
            var request = buildSyntheticRequest(projectId, branchName, commitHash);
            var snapshot = allFilePaths.isEmpty()
                    ? BranchFileOperationsService.BranchFileSnapshot.unavailable(false, "No reconciliation candidates")
                    : branchFileOperationsService.downloadBranchFileSnapshot(
                            ProjectVcsInfoRetriever.getVcsInfo(project), commitHash, evidencePaths);
            var prepared = allFilePaths.isEmpty()
                    ? new BranchFileOperationsService.PreparedBranchFiles(Set.of(), Map.of())
                    : branchFileOperationsService.prepareBranchFiles(evidencePaths, project, request, snapshot);
            long imported = analysisLockService.withOwnedLocks(List.of(lockKey.get()), () -> {
                Branch current = branchRepository.findById(branch.getId()).orElseThrow();
                long countBefore = branchIssueRepository.countAllByBranchId(branch.getId());
                for (var entry : prPlan.pathsByPr().entrySet()) {
                    branchIssueMappingService.mapCodeAnalysisIssuesToBranch(
                            entry.getValue(), prepared.existingFiles(), current, project, entry.getKey());
                }
                branchFileOperationsService.persistPreparedFiles(current, project, request, prepared);
                branchIssueReconciliationService.verifyIssueLineNumbersWithSnippets(prepared.contents().keySet(), project, current);
                refreshAndSaveIssueCounts(current);
                return Math.max(0, branchIssueRepository.countAllByBranchId(branch.getId()) - countBefore);
            });
            Branch reconciled = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
            long openBefore = reconciled.getIssues().stream().filter(issue -> !issue.isResolved())
                    .filter(issue -> AnalysisScopeFilter.scope(project).includes(issue.getFilePath())).count();
            requireOwnership(lease);
            var outcome = branchIssueReconciliationService.reanalyzeCandidateIssues(
                    allFilePaths, prepared.existingFiles(), branch, project, request, consumer,
                    prepared.contents(), null, snapshot.allowContentApiFallback(), action ->
                            analysisLockService.withOwnedLocks(List.of(lockKey.get()), () -> {
                                action.run();
                                Branch current = branchRepository.findById(branch.getId()).orElseThrow();
                                current.finishReconciliation(null);
                                branchRepository.save(current);
                                branchFileOperationsService.persistBranchFiles(prepared.existingFiles(), project, branchName);
                                return null;
                            }));
            if (!outcome.complete()) {
                analysisLockService.withOwnedLocks(List.of(lockKey.get()), () -> {
                    Branch current = branchRepository.findById(branch.getId()).orElseThrow();
                    current.finishReconciliation(outcome.diagnostic());
                    branchRepository.save(current);
                    return null;
                });
            }
            Branch finalBranch = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
            long resolvedAfter = finalBranch.getResolvedCount();
            long newlyResolved = Math.max(0, resolvedAfter - resolvedBefore);
            long totalAfter = finalBranch.getTotalIssues();
            String message = outcome.complete()
                    ? "Reconciliation complete: " + totalAfter + " open issues, " + newlyResolved + " newly resolved"
                    : outcome.diagnostic();
            EventNotificationEmitter.emitStatus(consumer, outcome.complete() ? "completed" : "reconciliation_pending", message);
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("status", outcome.complete() ? "completed" : "pending");
            result.put("reconciliationPending", !outcome.complete());
            result.put("branch", branchName);
            result.put("totalIssues", totalAfter);
            result.put("resolvedIssues", newlyResolved);
            result.put("resolvedIssuesBefore", resolvedBefore);
            result.put("resolvedIssuesAfter", resolvedAfter);
            result.put("openIssuesBefore", openBefore);
            result.put("openIssuesAfter", totalAfter);
            result.put("filesChecked", allFilePaths.size());
            result.put("mergedPrsScanned", prPlan.mergedPrCount());
            result.put("mergedPrsWithOpenIssues", prPlan.pathsByPr().size());
            result.put("prIssueFilesChecked", prPlan.pathsByPr().values().stream().flatMap(Set::stream).distinct().count());
            result.put("importedPrIssues", imported);
            addPrStatusSyncMetrics(result, prStatusSyncResult);
            result.put("message", message);
            return result;
        } catch (Exception e) {
            log.error("Full reconciliation failed for branch {}: {}", branchName, e.getMessage(), e);
            EventNotificationEmitter.emitStatus(consumer, "error",
                    "Full reconciliation failed: " + e.getMessage());
            throw e;
        } finally {
            try {
                analysisLockService.releaseLock(lockKey.get());
            } catch (RuntimeException unavailable) {
                log.warn("Full reconciliation lock cleanup deferred until lease expiry: {}", unavailable.getMessage());
            }
        }
    }

    private static void requireOwnership(AnalysisLockService.LockLease lease) throws IOException {
        if (lease == null || !lease.confirmOwnership()) {
            throw new IOException("Full branch reconciliation lost its lock lease; further writes stopped");
        }
    }

    private record MergedPrPlan(int mergedPrCount, Map<Long, Set<String>> pathsByPr) {}

    private MergedPrPlan prepareMergedPrIssues(Project project, Branch branch) {
        List<PullRequest> mergedPrs = pullRequestRepository.findByProjectIdAndTargetBranchNameAndState(
                project.getId(), branch.getBranchName(), PullRequestState.MERGED);
        Map<Long, Set<String>> pathsByPr = new LinkedHashMap<>();
        for (PullRequest pr : mergedPrs) {
            if (!isValidPrNumber(pr.getPrNumber())) continue;
            Set<String> paths = new LinkedHashSet<>(branchIssueMappingService.findPrIssuePaths(project.getId(), pr.getPrNumber()));
            AnalysisScopeFilter.retainIncluded(paths, project);
            if (!paths.isEmpty()) pathsByPr.put(pr.getPrNumber(), paths);
        }
        return new MergedPrPlan(mergedPrs.size(), pathsByPr);
    }

    private Branch refreshAndSaveIssueCounts(Branch branch) {
        Branch refreshed = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
        refreshed.updateIssueCounts();
        branchRepository.save(refreshed);
        return refreshed;
    }

    private String resolveCommitHash(Branch branch) {
        String hash = branch.getLastSuccessfulCommitHash();
        if (hash == null || hash.isBlank()) {
            hash = branch.getCommitHash();
        }
        if (hash == null || hash.isBlank()) {
            throw new IllegalStateException(
                    "Branch has no commit hash - it may not have been analyzed yet");
        }
        return hash;
    }

    private BranchProcessRequest buildSyntheticRequest(
            Long projectId,
            String branchName,
            String commitHash) {
        BranchProcessRequest req = new BranchProcessRequest();
        req.projectId = projectId;
        req.targetBranchName = branchName;
        req.commitHash = commitHash;
        req.analysisType = AnalysisType.BRANCH_ANALYSIS;
        return req;
    }

    private void addPrStatusSyncMetrics(
            Map<String, Object> result,
            PullRequestStatusSyncService.SyncResult prStatusSyncResult) {
        result.put("openPrsChecked", prStatusSyncResult.checked());
        result.put("openPrsStillOpen", prStatusSyncResult.stillOpen());
        result.put("openPrsMarkedMerged", prStatusSyncResult.markedMerged());
        result.put("openPrsMarkedDeclined", prStatusSyncResult.markedDeclined());
        result.put("openPrStatusSyncFailed", prStatusSyncResult.failed());
    }

    private static boolean isValidPrNumber(Long prNumber) {
        return prNumber != null && prNumber > 0;
    }

}
