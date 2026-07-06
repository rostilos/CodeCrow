package org.rostilos.codecrow.analysisengine.service.branch;

import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectValidationService;
import org.rostilos.codecrow.analysisengine.service.PullRequestStatusSyncService;
import org.rostilos.codecrow.analysisengine.util.ProjectVcsInfoRetriever;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
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

            PullRequestStatusSyncService.SyncResult prStatusSyncResult =
                    pullRequestStatusSyncService.syncOpenPullRequestStates(project, consumer);
            MergedPrSyncResult prSyncResult = syncMergedPrIssuesIntoBranch(
                    project, branch, commitHash, consumer);

            Branch branchBefore = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
            long resolvedBefore = branchBefore.getIssues().stream().filter(BranchIssue::isResolved).count();
            List<BranchIssue> allUnresolved = branchBefore.getIssues().stream()
                    .filter(bi -> !bi.isResolved())
                    .toList();
            long openBefore = allUnresolved.size();

            if (allUnresolved.isEmpty()) {
                String message = resolvedBefore > 0
                        ? "No unresolved issues to reconcile. " + resolvedBefore
                                + " issues were already resolved."
                        : "No unresolved issues to reconcile";
                EventNotificationEmitter.emitStatus(consumer, "completed", message);
                Map<String, Object> result = new LinkedHashMap<>();
                result.put("status", "completed");
                result.put("branch", branchName);
                result.put("totalIssues", 0);
                result.put("resolvedIssues", 0);
                result.put("resolvedIssuesBefore", resolvedBefore);
                result.put("resolvedIssuesAfter", resolvedBefore);
                result.put("openIssuesBefore", 0);
                result.put("openIssuesAfter", 0);
                result.put("mergedPrsScanned", prSyncResult.mergedPrCount());
                result.put("mergedPrsWithOpenIssues", prSyncResult.prsWithOpenIssues());
                result.put("prIssueFilesChecked", prSyncResult.fileCount());
                result.put("importedPrIssues", prSyncResult.importedIssueCount());
                addPrStatusSyncMetrics(result, prStatusSyncResult);
                result.put("filesChecked", 0);
                result.put("message", message);
                return result;
            }

            Set<String> allFilePaths = allUnresolved.stream()
                    .map(BranchIssue::getFilePath)
                    .filter(fp -> fp != null && !fp.isBlank())
                    .collect(Collectors.toSet());

            log.info("Full reconciliation: {} unresolved issues across {} files (branch={})",
                    allUnresolved.size(), allFilePaths.size(), branchName);

            EventNotificationEmitter.emitStatus(consumer, "checking_files",
                    "Checking " + allFilePaths.size() + " files with "
                            + allUnresolved.size() + " unresolved issues");

            VcsRepoInfoImpl vcsRepoInfoImpl = ProjectVcsInfoRetriever.getVcsInfo(project);
            Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                    vcsRepoInfoImpl, commitHash, allFilePaths);
            log.info("Full reconciliation archive: {} files extracted for {} requested",
                    archiveContents.size(), allFilePaths.size());

            Set<String> filesExistingInBranch = branchFileOperationsService.updateBranchFiles(
                    allFilePaths, project, branchName, archiveContents);

            EventNotificationEmitter.emitStatus(consumer, "updating_snapshots",
                    "Updating file content snapshots for " + filesExistingInBranch.size()
                            + " files");

            BranchProcessRequest syntheticRequest = buildSyntheticRequest(projectId, branchName, commitHash);

            branchFileOperationsService.updateFileSnapshotsForBranch(
                    filesExistingInBranch, project, syntheticRequest, archiveContents);

            EventNotificationEmitter.emitStatus(consumer, "reanalyzing_issues",
                    "Reanalyzing " + allUnresolved.size() + " issues across "
                            + allFilePaths.size() + " files");

            branchIssueReconciliationService.reanalyzeCandidateIssues(
                    allFilePaths, filesExistingInBranch, branch, project,
                    syntheticRequest, consumer, archiveContents);

            Branch branchForVerify = branchRepository.findByProjectIdAndBranchName(projectId, branchName)
                    .orElse(branch);
            branchIssueReconciliationService.verifyIssueLineNumbersWithSnippets(
                    allFilePaths, project, branchForVerify);

            Branch finalBranch = refreshAndSaveIssueCounts(branch);
            long resolvedAfter = finalBranch.getResolvedCount();
            long newlyResolved = Math.max(0, resolvedAfter - resolvedBefore);
            long totalAfter = finalBranch.getTotalIssues();

            String message = String.format(
                    "Reconciliation complete: %d open issues remaining, %d newly resolved, %d total resolved, %d files checked, %d PR issues imported",
                    totalAfter, newlyResolved, resolvedAfter, allFilePaths.size(),
                    prSyncResult.importedIssueCount());

            log.info(
                    "Full reconciliation complete: branch={}, openBefore={}, openAfter={}, resolvedBefore={}, resolvedAfter={}, newlyResolved={}, prStatusesMerged={}, prStatusesDeclined={}",
                    branchName, openBefore, totalAfter, resolvedBefore, resolvedAfter,
                    newlyResolved, prStatusSyncResult.markedMerged(), prStatusSyncResult.markedDeclined());

            EventNotificationEmitter.emitStatus(consumer, "completed", message);

            Map<String, Object> result = new LinkedHashMap<>();
            result.put("status", "completed");
            result.put("branch", branchName);
            result.put("totalIssues", totalAfter);
            result.put("resolvedIssues", newlyResolved);
            result.put("resolvedIssuesBefore", resolvedBefore);
            result.put("resolvedIssuesAfter", resolvedAfter);
            result.put("openIssuesBefore", openBefore);
            result.put("openIssuesAfter", totalAfter);
            result.put("filesChecked", allFilePaths.size());
            result.put("mergedPrsScanned", prSyncResult.mergedPrCount());
            result.put("mergedPrsWithOpenIssues", prSyncResult.prsWithOpenIssues());
            result.put("prIssueFilesChecked", prSyncResult.fileCount());
            result.put("importedPrIssues", prSyncResult.importedIssueCount());
            addPrStatusSyncMetrics(result, prStatusSyncResult);
            result.put("message", message);
            return result;
        } catch (Exception e) {
            log.error("Full reconciliation failed for branch {}: {}", branchName, e.getMessage(), e);
            EventNotificationEmitter.emitStatus(consumer, "error",
                    "Full reconciliation failed: " + e.getMessage());
            throw e;
        } finally {
            analysisLockService.releaseLock(lockKey.get());
        }
    }

    private MergedPrSyncResult syncMergedPrIssuesIntoBranch(
            Project project,
            Branch branch,
            String commitHash,
            Consumer<Map<String, Object>> consumer) throws IOException {
        List<PullRequest> mergedPrs = pullRequestRepository.findByProjectIdAndTargetBranchNameAndState(
                project.getId(), branch.getBranchName(), PullRequestState.MERGED);
        if (mergedPrs.isEmpty()) {
            EventNotificationEmitter.emitStatus(consumer, "sync_pr_issues",
                    "No merged PRs recorded for branch: " + branch.getBranchName());
            return MergedPrSyncResult.empty();
        }

        Map<Long, Set<String>> issuePathsByPr = new LinkedHashMap<>();
        Set<String> allPrIssuePaths = new LinkedHashSet<>();
        for (PullRequest pr : mergedPrs) {
            if (!isValidPrNumber(pr.getPrNumber())) {
                continue;
            }
            Set<String> paths = branchIssueMappingService.findPrIssuePaths(
                    project.getId(), pr.getPrNumber());
            if (paths == null || paths.isEmpty()) {
                continue;
            }
            Set<String> orderedPaths = new LinkedHashSet<>(paths);
            issuePathsByPr.put(pr.getPrNumber(), orderedPaths);
            allPrIssuePaths.addAll(orderedPaths);
        }

        if (allPrIssuePaths.isEmpty()) {
            EventNotificationEmitter.emitStatus(consumer, "sync_pr_issues",
                    "Merged PR scan complete: no unresolved PR issues to import");
            return new MergedPrSyncResult(mergedPrs.size(), 0, 0, 0);
        }

        EventNotificationEmitter.emitStatus(consumer, "sync_pr_issues",
                "Syncing unresolved issues from " + issuePathsByPr.size()
                        + " merged PRs across " + allPrIssuePaths.size() + " files");

        long beforeCount = branchIssueRepository.countAllByBranchId(branch.getId());
        VcsRepoInfoImpl vcsRepoInfoImpl = ProjectVcsInfoRetriever.getVcsInfo(project);
        Map<String, String> archiveContents = branchFileOperationsService.downloadBranchArchive(
                vcsRepoInfoImpl, commitHash, allPrIssuePaths);
        Set<String> filesExistingInBranch = branchFileOperationsService.updateBranchFiles(
                allPrIssuePaths, project, branch.getBranchName(), archiveContents);

        for (Map.Entry<Long, Set<String>> entry : issuePathsByPr.entrySet()) {
            Set<String> existingPrPaths = entry.getValue().stream()
                    .filter(filesExistingInBranch::contains)
                    .collect(Collectors.toCollection(LinkedHashSet::new));
            if (existingPrPaths.isEmpty()) {
                continue;
            }
            branchIssueMappingService.mapCodeAnalysisIssuesToBranch(
                    existingPrPaths, filesExistingInBranch, branch, project, entry.getKey());
        }

        long afterCount = branchIssueRepository.countAllByBranchId(branch.getId());
        long imported = Math.max(0, afterCount - beforeCount);
        EventNotificationEmitter.emitStatus(consumer, "sync_pr_issues_complete",
                "Merged PR issue sync complete: " + imported + " branch issues imported");
        return new MergedPrSyncResult(
                mergedPrs.size(), issuePathsByPr.size(), allPrIssuePaths.size(), imported);
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

    private record MergedPrSyncResult(
            int mergedPrCount,
            int prsWithOpenIssues,
            int fileCount,
            long importedIssueCount) {

        static MergedPrSyncResult empty() {
            return new MergedPrSyncResult(0, 0, 0, 0);
        }
    }
}
