package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchFile;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchFileRepository;
import org.rostilos.codecrow.core.service.FileSnapshotService;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;
import org.rostilos.codecrow.core.util.tracking.TrackingConfidence;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectService;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.analysisengine.service.gitgraph.GitGraphSyncService;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import okhttp3.OkHttpClient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.*;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import org.rostilos.codecrow.core.model.gitgraph.CommitNode;

/**
 * Generic service that handles branch analysis after PR merges or direct commits.
 * Uses VCS-specific services via VcsServiceFactory for provider-specific operations.
 */
@Service
public class BranchAnalysisProcessor {

    private static final Logger log = LoggerFactory.getLogger(BranchAnalysisProcessor.class);

    private final ProjectService projectService;
    private final BranchFileRepository branchFileRepository;
    private final BranchRepository branchRepository;
    private final CodeAnalysisIssueRepository codeAnalysisIssueRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final VcsClientProvider vcsClientProvider;
    private final AiAnalysisClient aiAnalysisClient;
    private final VcsServiceFactory vcsServiceFactory;
    private final AnalysisLockService analysisLockService;
    private final GitGraphSyncService gitGraphSyncService;
    private final FileSnapshotService fileSnapshotService;
    private final CodeAnalysisRepository codeAnalysisRepository;
    
    /** Optional RAG operations service - can be null if RAG is not enabled */
    private final RagOperationsService ragOperationsService;

    private static final Pattern DIFF_GIT_PATTERN = Pattern.compile("^diff --git\\s+a/(\\S+)\\s+b/(\\S+)");

    public BranchAnalysisProcessor(
            ProjectService projectService,
            BranchFileRepository branchFileRepository,
            BranchRepository branchRepository,
            CodeAnalysisIssueRepository codeAnalysisIssueRepository,
            BranchIssueRepository branchIssueRepository,
            VcsClientProvider vcsClientProvider,
            AiAnalysisClient aiAnalysisClient,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService,
            GitGraphSyncService gitGraphSyncService,
            FileSnapshotService fileSnapshotService,
            CodeAnalysisRepository codeAnalysisRepository,
            @Autowired(required = false) RagOperationsService ragOperationsService
    ) {
        this.projectService = projectService;
        this.branchFileRepository = branchFileRepository;
        this.branchRepository = branchRepository;
        this.codeAnalysisIssueRepository = codeAnalysisIssueRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.aiAnalysisClient = aiAnalysisClient;
        this.vcsServiceFactory = vcsServiceFactory;
        this.analysisLockService = analysisLockService;
        this.gitGraphSyncService = gitGraphSyncService;
        this.fileSnapshotService = fileSnapshotService;
        this.codeAnalysisRepository = codeAnalysisRepository;
        this.ragOperationsService = ragOperationsService;
    }

    /**
     * Helper record to hold VCS info.
     */
    public record VcsInfo(VcsConnection vcsConnection, String workspace, String repoSlug) {}


    /**
     * Get VCS info from project using the unified accessor.
     */
    public VcsInfo getVcsInfo(Project project) {
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null && vcsInfo.getVcsConnection() != null) {
            return new VcsInfo(
                    vcsInfo.getVcsConnection(),
                    vcsInfo.getRepoWorkspace(),
                    vcsInfo.getRepoSlug()
            );
        }
        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    private EVcsProvider getVcsProvider(Project project) {
        VcsInfo vcsInfo = getVcsInfo(project);
        return vcsInfo.vcsConnection().getProviderType();
    }

    public Map<String, Object> process(BranchProcessRequest request, Consumer<Map<String, Object>> consumer) throws IOException {
        Project project = projectService.getProjectWithConnections(request.getProjectId());

        Optional<String> lockKey = analysisLockService.acquireLockWithWait(
                project,
                request.getTargetBranchName(),
                AnalysisLockType.BRANCH_ANALYSIS,
                request.getCommitHash(),
                null,
                consumer
        );

        if (lockKey.isEmpty()) {
            log.warn("Branch analysis already in progress for project={}, branch={}",
                    project.getId(), request.getTargetBranchName());
            throw new AnalysisLockedException(
                    AnalysisLockType.BRANCH_ANALYSIS.name(),
                    request.getTargetBranchName(),
                    project.getId()
            );
        }

        // Declared outside try/catch so both success and failure paths can access it
        List<String> unanalyzedCommits = Collections.emptyList();

        try {
            // Early branch lookup — needed for delta diff strategy and dedup check
            Optional<Branch> existingBranchOpt = branchRepository.findByProjectIdAndBranchName(
                    project.getId(), request.getTargetBranchName());

            // Check if this commit was already SUCCESSFULLY analyzed for this branch.
            // Uses lastSuccessfulCommitHash (not commitHash) so that failed attempts are re-processed
            // via delta diff on the next trigger.
            if (request.getCommitHash() != null && existingBranchOpt.isPresent()) {
                String lastSuccess = existingBranchOpt.get().getLastSuccessfulCommitHash();
                if (request.getCommitHash().equals(lastSuccess)) {
                    log.info("Skipping branch analysis - commit {} already successfully analyzed for branch {} (project={})",
                            request.getCommitHash(), request.getTargetBranchName(), project.getId());

                    // Even though the commit is already analyzed, refresh file snapshots
                    // so the source code viewer shows current content (especially after
                    // PR merges that change file content without changing the commit hash
                    // on the target branch, or when snapshots were missing from the original analysis).
                    try {
                        VcsInfo vcsInfo = getVcsInfo(project);
                        EVcsProvider provider = getVcsProvider(project);
                        VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);
                        OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());

                        // Get all files tracked for this branch and update their snapshots
                        Set<String> branchFiles = branchFileRepository
                                .findByProjectIdAndBranchName(project.getId(), request.getTargetBranchName())
                                .stream()
                                .map(bf -> bf.getFilePath())
                                .collect(Collectors.toSet());

                        if (!branchFiles.isEmpty()) {
                            updateFileSnapshotsForBranch(branchFiles, project, request, vcsInfo, operationsService, client);
                        }
                    } catch (Exception snapEx) {
                        log.warn("Failed to refresh file snapshots on skip path (non-critical): {}", snapEx.getMessage());
                    }

                    consumer.accept(Map.of(
                            "type", "status",
                            "state", "skipped",
                            "message", "Commit already successfully analyzed for this branch"
                    ));
                    return Map.of(
                            "status", "skipped",
                            "reason", "commit_already_analyzed",
                            "branch", request.getTargetBranchName(),
                            "commitHash", request.getCommitHash()
                    );
                }
            }

            consumer.accept(Map.of(
                    "type", "status",
                    "state", "started",
                    "message", "Branch analysis started for branch: " + request.getTargetBranchName()
            ));

            VcsInfo vcsInfo = getVcsInfo(project);

            OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());
            
            // === DAG-DRIVEN ANALYSIS ===
            // 1. Sync git graph and get the full node map
            Map<String, CommitNode> nodeMap = Collections.emptyMap();
            String dagDiffBase = null;
            
            try {
                nodeMap = gitGraphSyncService.syncBranchGraph(
                        project,
                        vcsClientProvider.getClient(vcsInfo.vcsConnection()),
                        request.getTargetBranchName(),
                        100
                );

                if (!nodeMap.isEmpty() && request.getCommitHash() != null) {
                    // 2. Walk DAG from HEAD backwards to find unanalyzed commits
                    // Returns null if HEAD not found in node map (fall through to legacy),
                    // empty list if HEAD is already analyzed (skip), or non-empty list of commits to analyze.
                    List<String> dagResult = gitGraphSyncService.findUnanalyzedCommitRange(
                            nodeMap, request.getCommitHash());
                    
                    if (dagResult == null) {
                        // HEAD commit not found in node map — cannot determine DAG state.
                        // Fall through to legacy diff strategy.
                        log.info("DAG could not resolve HEAD commit {} — falling back to legacy analysis",
                                request.getCommitHash());
                        nodeMap = Collections.emptyMap();
                    } else if (dagResult.isEmpty()) {
                        // HEAD is already analyzed in the DAG — skip entirely
                        log.info("DAG shows HEAD commit {} is already analyzed — skipping branch analysis",
                                request.getCommitHash());
                        consumer.accept(Map.of(
                                "type", "status",
                                "state", "skipped",
                                "message", "All commits already analyzed (DAG check)"
                        ));
                        return Map.of(
                                "status", "skipped",
                                "reason", "dag_already_analyzed",
                                "branch", request.getTargetBranchName(),
                                "commitHash", request.getCommitHash()
                        );
                    } else {
                        unanalyzedCommits = dagResult;
                    }
                    
                    // 3. Find the nearest analyzed ancestor — this is the diff base
                    dagDiffBase = gitGraphSyncService.findAnalyzedAncestor(
                            nodeMap, request.getCommitHash());
                    
                    log.info("DAG analysis: {} unanalyzed commits, diff base = {} (branch={})",
                            unanalyzedCommits.size(),
                            dagDiffBase != null ? dagDiffBase.substring(0, Math.min(7, dagDiffBase.length())) : "none (first analysis)",
                            request.getTargetBranchName());
                }
            } catch (Exception e) {
                log.warn("Git graph sync/walk failed for branch {} — falling back to legacy diff strategy: {}",
                        request.getTargetBranchName(), e.getMessage());
                // Reset to empty so we fall through to the legacy 3-tier strategy
                nodeMap = Collections.emptyMap();
                unanalyzedCommits = Collections.emptyList();
                dagDiffBase = null;
            }

            consumer.accept(Map.of(
                    "type", "status",
                    "state", "fetching_diff",
                    "message", "Fetching diff"
            ));

            EVcsProvider provider = getVcsProvider(project);
            VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);

            // If sourcePrNumber is not set, try to look it up from the commit
            // This handles cases where branch analysis is triggered by push events
            Long prNumber = request.getSourcePrNumber();
            if (prNumber == null && request.getCommitHash() != null) {
                try {
                    prNumber = operationsService.findPullRequestForCommit(
                            client,
                            vcsInfo.workspace(),
                            vcsInfo.repoSlug(),
                            request.getCommitHash()
                    );
                    if (prNumber != null) {
                        log.info("Found PR #{} for commit {} via API lookup", prNumber, request.getCommitHash());
                        request.sourcePrNumber = prNumber; // Update request for later use
                    }
                } catch (Exception e) {
                    log.debug("Could not look up PR for commit {}: {}", request.getCommitHash(), e.getMessage());
                }
            }

            // 3-tier diff strategy (enhanced by DAG):
            // Tier 0 (NEW): DAG-derived diff base — the nearest analyzed ancestor in the commit graph.
            //         More accurate than lastSuccessfulCommitHash because it follows actual commit topology.
            // Tier 1: Delta diff (lastSuccessfulCommitHash..HEAD) — fallback if DAG is unavailable
            // Tier 2: PR diff — for first-ever analysis triggered by PR merge
            // Tier 3: Single commit diff — for first-ever analysis from direct push
            String rawDiff = null;
            String lastSuccessfulCommit = existingBranchOpt.map(Branch::getLastSuccessfulCommitHash).orElse(null);

            // Tier 0: DAG-derived diff base (preferred)
            if (dagDiffBase != null && request.getCommitHash() != null && !dagDiffBase.equals(request.getCommitHash())) {
                try {
                    rawDiff = operationsService.getCommitRangeDiff(
                            client, vcsInfo.workspace(), vcsInfo.repoSlug(),
                            dagDiffBase, request.getCommitHash());
                    if (rawDiff != null && rawDiff.isBlank()) {
                        // Empty diff typically means a merge commit where the DAG ancestor is
                        // a PR branch commit (same content as the merge). Fall through to the
                        // next tier which can use the PR diff with actual changes.
                        log.info("DAG-based diff ({}..{}) returned empty (likely merge commit) — falling through to next tier",
                                dagDiffBase.substring(0, Math.min(7, dagDiffBase.length())),
                                request.getCommitHash().substring(0, Math.min(7, request.getCommitHash().length())));
                        rawDiff = null;
                    } else {
                        log.info("Fetched DAG-based diff ({}..{}) — covers {} unanalyzed commits",
                                dagDiffBase.substring(0, Math.min(7, dagDiffBase.length())),
                                request.getCommitHash().substring(0, Math.min(7, request.getCommitHash().length())),
                                unanalyzedCommits.size());
                    }
                } catch (IOException e) {
                    log.warn("DAG-based diff failed (ancestor {} may be unreachable), falling back: {}",
                            dagDiffBase.substring(0, Math.min(7, dagDiffBase.length())), e.getMessage());
                    rawDiff = null;
                }
            }

            // Tier 1: Legacy delta diff (fallback when DAG is unavailable or failed)
            if (rawDiff == null && lastSuccessfulCommit != null && !lastSuccessfulCommit.equals(request.getCommitHash())) {
                try {
                    rawDiff = operationsService.getCommitRangeDiff(
                            client, vcsInfo.workspace(), vcsInfo.repoSlug(),
                            lastSuccessfulCommit, request.getCommitHash());
                    if (rawDiff != null && rawDiff.isBlank()) {
                        log.info("Delta diff ({}..{}) returned empty — falling through to next tier",
                                lastSuccessfulCommit.substring(0, Math.min(7, lastSuccessfulCommit.length())),
                                request.getCommitHash().substring(0, Math.min(7, request.getCommitHash().length())));
                        rawDiff = null;
                    } else {
                        log.info("Fetched delta diff ({}..{}) for branch analysis — captures all changes since last success",
                                lastSuccessfulCommit.substring(0, Math.min(7, lastSuccessfulCommit.length())),
                                request.getCommitHash().substring(0, Math.min(7, request.getCommitHash().length())));
                    }
                } catch (IOException e) {
                    log.warn("Delta diff failed (base commit {} may no longer exist), falling back: {}",
                            lastSuccessfulCommit.substring(0, Math.min(7, lastSuccessfulCommit.length())), e.getMessage());
                    rawDiff = null; // Force fallback to tier 2/3
                }
            }

            if (rawDiff == null && prNumber != null) {
                rawDiff = operationsService.getPullRequestDiff(
                        client, vcsInfo.workspace(), vcsInfo.repoSlug(),
                        String.valueOf(prNumber));
                log.info("Fetched PR #{} diff for branch analysis (first analysis or delta fallback)", prNumber);
            }

            if (rawDiff == null) {
                rawDiff = operationsService.getCommitDiff(
                        client, vcsInfo.workspace(), vcsInfo.repoSlug(),
                        request.getCommitHash());
                log.info("Fetched commit {} diff for branch analysis (first analysis, no delta or PR context)",
                        request.getCommitHash());
            }

            Set<String> changedFiles = parseFilePathsFromDiff(rawDiff);

            consumer.accept(Map.of(
                    "type", "status",
                    "state", "analyzing_files",
                    "message", "Analyzing " + changedFiles.size() + " changed files"
            ));

            // Reconcile line numbers on BranchIssues AFTER they've been created/deep-copied.
            // The old flow reconciled on CodeAnalysisIssue before BranchIssue creation;
            // the new flow creates independent BranchIssues first, then adjusts their line numbers.

            Set<String> existingFiles = updateBranchFiles(changedFiles, project, request.getTargetBranchName());
            Branch branch = createOrUpdateProjectBranch(project, request, existingBranchOpt.orElse(null));

            mapCodeAnalysisIssuesToBranch(changedFiles, existingFiles, branch, project);

            // Now reconcile on the independent BranchIssues (not CodeAnalysisIssue)
            reconcileIssueLineNumbers(rawDiff, changedFiles, branch);
            
            // Always update branch issue counts after mapping (even on first analysis)
            // Previously this was only done in reanalyzeCandidateIssues() which could be skipped
            Branch refreshedBranch = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
            refreshedBranch.updateIssueCounts();
            branchRepository.save(refreshedBranch);
            log.info("Updated branch issue counts after mapping: total={}, high={}, medium={}, low={}, resolved={}",
                    refreshedBranch.getTotalIssues(), refreshedBranch.getHighSeverityCount(), 
                    refreshedBranch.getMediumSeverityCount(), refreshedBranch.getLowSeverityCount(), 
                    refreshedBranch.getResolvedCount());
            
            reanalyzeCandidateIssues(changedFiles, existingFiles, refreshedBranch, project, request, consumer);

            // === Update file snapshots for the source code viewer ===
            // When branch analysis reconciles issue line numbers, the stored file content
            // may be stale (from the original PR analysis). Fetch current file content
            // for changed files and update snapshots in the latest analysis.
            updateFileSnapshotsForBranch(existingFiles, project, request, vcsInfo, operationsService, client);

            // === Snippet-based line verification ===
            // After file snapshots are updated with current content, re-verify all
            // branch issue line numbers for changed files using their persisted codeSnippet.
            // This catches any drift that diff-based remapping or IssueTracker missed.
            // Re-fetch the branch to get the latest state after reanalysis
            Branch branchForVerify = branchRepository.findByProjectIdAndBranchName(
                    project.getId(), request.getTargetBranchName()).orElse(refreshedBranch);
            verifyIssueLineNumbersWithSnippets(changedFiles, project, branchForVerify);

            // Incremental RAG update for merged PR
            performIncrementalRagUpdate(request, project, vcsInfo, rawDiff, consumer);

            // Mark branch as HEALTHY — all analysis steps completed successfully.
            // Sets lastSuccessfulCommitHash so future analyses use delta diff from this point.
            branchRepository.findByProjectIdAndBranchName(project.getId(), request.getTargetBranchName())
                    .ifPresent(b -> {
                        b.markHealthy(request.getCommitHash());
                        branchRepository.save(b);
                    });

            // === DAG: Mark all covered commits as ANALYZED ===
            if (!unanalyzedCommits.isEmpty()) {
                try {
                    gitGraphSyncService.markCommitsAnalyzed(project.getId(), unanalyzedCommits);
                    log.info("DAG: Marked {} commits as ANALYZED after successful branch analysis (branch={})",
                            unanalyzedCommits.size(), request.getTargetBranchName());
                } catch (Exception e) {
                    log.warn("Failed to mark commits as ANALYZED in DAG (non-critical): {}", e.getMessage());
                }
            }

            log.info("Reconciliation finished (Branch: {}, Commit: {}, status: HEALTHY)",
                    request.getTargetBranchName(),
                    request.getCommitHash());

            return Map.of(
                    "status", "accepted",
                    "cached", false,
                    "branch", request.getTargetBranchName()
            );
        } catch (Exception e) {
            // Mark branch as STALE so the health scheduler knows to retry.
            // If the branch doesn't exist yet (first analysis failed before creation), this is a no-op.
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

            // === DAG: Mark covered commits as FAILED (eligible for retry) ===
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
                    request.getTargetBranchName(),
                    request.getCommitHash(),
                    e.getMessage());
            throw e;
        } finally {
            analysisLockService.releaseLock(lockKey.get());
        }
    }

    public Set<String> parseFilePathsFromDiff(String rawDiff) {
        Set<String> files = new HashSet<>();
        if (rawDiff == null || rawDiff.isBlank()) return files;

        String[] lines = rawDiff.split("\\r?\\n");
        for (String line : lines) {
            Matcher m = DIFF_GIT_PATTERN.matcher(line);
            if (m.find()) {
                // prefer the 'b/...' path (second captured group)
                String path = m.group(2);
                if (path != null && !path.isBlank()) {
                    files.add(path);
                }
            }
        }
        return files;
    }

    /**
     * Updates branch file records for changed files.
     * Now also fetches file content to compute content hashes for tracking.
     * @return the set of file paths confirmed to exist in the branch (used to avoid redundant API calls)
     */
    private Set<String> updateBranchFiles(Set<String> changedFiles, Project project, String branchName) {
        VcsInfo vcsInfo = getVcsInfo(project);
        EVcsProvider provider = getVcsProvider(project);
        VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);
        Set<String> filesExistingInBranch = new HashSet<>();

        // Try to find the branch entity for FK linkage
        Branch branchEntity = branchRepository.findByProjectIdAndBranchName(project.getId(), branchName)
                .orElse(null);

        for (String filePath : changedFiles) {
            try {
                OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());

                boolean fileExistsInBranch = operationsService.checkFileExistsInBranch(
                        client,
                        vcsInfo.workspace(),
                        vcsInfo.repoSlug(),
                        branchName,
                        filePath
                );

                if (!fileExistsInBranch) {
                    log.debug("Skipping file {} - does not exist in branch {}", filePath, branchName);
                    continue;
                }
                filesExistingInBranch.add(filePath);
            } catch (Exception e) {
                log.warn("Failed to check file existence for {} in branch {}: {}. Proceeding anyway.",
                    filePath, branchName, e.getMessage());
                filesExistingInBranch.add(filePath);
            }

            // Count unresolved BranchIssues for this file (authoritative, no dedup needed)
            long unresolvedCount = 0;
            if (branchEntity != null) {
                unresolvedCount = branchIssueRepository
                        .findUnresolvedByBranchIdAndFilePath(branchEntity.getId(), filePath)
                        .size();
            } else {
                // Fallback: count from CodeAnalysisIssue (branch entity not yet created)
                List<CodeAnalysisIssue> relatedIssues = codeAnalysisIssueRepository
                        .findByProjectIdAndFilePath(project.getId(), filePath);
                unresolvedCount = relatedIssues.stream()
                        .filter(i -> !i.isResolved())
                        .filter(i -> {
                            CodeAnalysis a = i.getAnalysis();
                            return a != null && (branchName.equals(a.getBranchName()) ||
                                    branchName.equals(a.getSourceBranchName()));
                        })
                        .count();
            }

            Optional<BranchFile> projectFileOptional = branchFileRepository
                    .findByProjectIdAndBranchNameAndFilePath(project.getId(), branchName, filePath);
            if (projectFileOptional.isPresent()) {
                BranchFile branchFile = projectFileOptional.get();
                branchFile.setIssueCount((int) unresolvedCount);
                // Link branch FK if not already set
                if (branchFile.getBranch() == null && branchEntity != null) {
                    branchFile.setBranch(branchEntity);
                }
                branchFileRepository.save(branchFile);
            } else {
                BranchFile branchFile = new BranchFile();
                branchFile.setProject(project);
                branchFile.setBranchName(branchName);
                branchFile.setFilePath(filePath);
                branchFile.setIssueCount((int) unresolvedCount);
                if (branchEntity != null) {
                    branchFile.setBranch(branchEntity);
                }
                branchFileRepository.save(branchFile);
            }
        }
        return filesExistingInBranch;
    }

    private Branch createOrUpdateProjectBranch(Project project, BranchProcessRequest request, Branch existingBranch) {
        Branch branch;
        if (existingBranch != null) {
            branch = existingBranch;
        } else {
            branch = new Branch();
            branch.setProject(project);
            branch.setBranchName(request.getTargetBranchName());
        }
        branch.setCommitHash(request.getCommitHash());
        return branchRepository.save(branch);
    }

    private void mapCodeAnalysisIssuesToBranch(Set<String> changedFiles, Set<String> filesExistingInBranch,
                                               Branch branch, Project project) {
        for (String filePath : changedFiles) {
            // Use cached file existence from updateBranchFiles to avoid redundant API calls
            if (!filesExistingInBranch.contains(filePath)) {
                log.debug("Skipping issue mapping for file {} - does not exist in branch {} (cached)",
                        filePath, branch.getBranchName());
                continue;
            }

            List<CodeAnalysisIssue> allIssues = codeAnalysisIssueRepository.findByProjectIdAndFilePath(project.getId(), filePath);

            List<CodeAnalysisIssue> branchSpecificIssues = allIssues.stream()
                    .filter(issue -> {
                        CodeAnalysis analysis = issue.getAnalysis();
                        if (analysis == null) return false;

                        return branch.getBranchName().equals(analysis.getBranchName()) ||
                               branch.getBranchName().equals(analysis.getSourceBranchName());
                    })
                    .toList();

            // Content-based deduplication: build a map of existing BranchIssues by content fingerprint
            // to prevent the same logical issue from being linked multiple times across analyses.
            List<BranchIssue> existingBranchIssues = branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branch.getId(), filePath);
            Map<String, BranchIssue> contentFpMap = new HashMap<>();
            Map<String, BranchIssue> legacyKeyMap = new HashMap<>();
            for (BranchIssue bi : existingBranchIssues) {
                if (bi.getContentFingerprint() != null) {
                    contentFpMap.putIfAbsent(bi.getContentFingerprint(), bi);
                }
                String legacyKey = buildLegacyContentKey(bi);
                legacyKeyMap.putIfAbsent(legacyKey, bi);
            }

            // Also track origin issue IDs to avoid re-copying the same CAI
            Set<Long> linkedOriginIds = existingBranchIssues.stream()
                    .filter(bi -> bi.getOriginIssue() != null)
                    .map(bi -> bi.getOriginIssue().getId())
                    .collect(Collectors.toSet());

            int skipped = 0;
            for (CodeAnalysisIssue issue : branchSpecificIssues) {
                // Tier 1: origin ID match — same CodeAnalysisIssue already deep-copied
                if (linkedOriginIds.contains(issue.getId())) {
                    // Update severity on existing BranchIssue if it changed
                    branchIssueRepository.findByBranchIdAndOriginIssueId(branch.getId(), issue.getId())
                            .ifPresent(existing -> {
                                if (existing.getSeverity() != issue.getSeverity()) {
                                    existing.setSeverity(issue.getSeverity());
                                    branchIssueRepository.saveAndFlush(existing);
                                }
                            });
                    continue;
                }

                // Tier 2: content fingerprint dedup — same logical issue from a different analysis
                if (issue.getContentFingerprint() != null && contentFpMap.containsKey(issue.getContentFingerprint())) {
                    skipped++;
                    continue;
                }

                // Tier 3: legacy key dedup for pre-tracking issues
                String legacyKey = buildLegacyContentKeyFromCAI(issue);
                if (legacyKeyMap.containsKey(legacyKey)) {
                    skipped++;
                    continue;
                }

                // No match — create new BranchIssue as a full deep copy
                BranchIssue bi = BranchIssue.fromCodeAnalysisIssue(issue, branch);
                branchIssueRepository.saveAndFlush(bi);

                // Register in maps so subsequent issues in this batch also dedup
                if (bi.getContentFingerprint() != null) {
                    contentFpMap.put(bi.getContentFingerprint(), bi);
                }
                legacyKeyMap.put(buildLegacyContentKey(bi), bi);
                linkedOriginIds.add(issue.getId());
            }

            if (skipped > 0) {
                log.debug("Skipped {} duplicate issue(s) for file {} in branch {}",
                        skipped, filePath, branch.getBranchName());
            }
        }
    }

    /**
     * Builds a legacy content key for deduplication of branch issues (pre-tracking fallback).
     */
    private String buildLegacyContentKey(BranchIssue bi) {
        return bi.getFilePath() + ":" +
               bi.getLineNumber() + ":" +
               bi.getSeverity() + ":" +
               bi.getIssueCategory();
    }

    private String buildLegacyContentKeyFromCAI(CodeAnalysisIssue issue) {
        return issue.getFilePath() + ":" +
               issue.getLineNumber() + ":" +
               issue.getSeverity() + ":" +
               issue.getIssueCategory();
    }

    // ────────────────── Diff-based line-number reconciliation ──────────────────
    //
    // When a file changes in the branch, existing issues still point to old line
    // numbers. Before counting or deduplicating we walk the unified-diff hunks
    // and compute where each old line moved to in the new version, then persist
    // the updated lineNumber on each CodeAnalysisIssue entity.
    // ──────────────────────────────────────────────────────────────────────────

    /** Pattern for unified-diff hunk headers: @@ -oldStart[,oldCount] +newStart[,newCount] @@ */
    private static final Pattern HUNK_HEADER = Pattern.compile(
            "^@@\\s+-(?:(\\d+))(?:,(\\d+))?\\s+\\+(?:(\\d+))(?:,(\\d+))?\\s+@@");

    /**
     * For every changed file that still exists in the branch, update the
     * {@code lineNumber} (and {@code currentLineNumber}) of each unresolved
     * {@link BranchIssue} so that it reflects the line's position <em>after</em>
     * the diff was applied.
     * <p>
     * IMPORTANT: This method now mutates BranchIssue's own fields only.
     * CodeAnalysisIssue records remain immutable historical records.
     * <p>
     * If an issue's line was deleted (no corresponding new line), we leave the
     * line number unchanged — the AI reconciliation step will handle resolution.
     *
     * @param rawDiff       full unified diff (may contain multiple files)
     * @param changedFiles  set of file paths that changed
     * @param branch        current branch entity
     */
    private void reconcileIssueLineNumbers(String rawDiff, Set<String> changedFiles,
                                           Branch branch) {
        if (rawDiff == null || rawDiff.isBlank()) return;

        Map<String, String> perFileDiffs = splitDiffByFile(rawDiff);

        for (String filePath : changedFiles) {
            String fileDiff = perFileDiffs.get(filePath);
            if (fileDiff == null) continue; // file mentioned but no actual hunks (e.g. binary)

            List<BranchIssue> issues = branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branch.getId(), filePath);

            if (issues.isEmpty()) continue;

            List<int[]> hunks = parseHunks(fileDiff);
            if (hunks.isEmpty()) continue;

            int updated = 0;
            for (BranchIssue bi : issues) {
                Integer lineNum = bi.getCurrentLineNumber() != null
                        ? bi.getCurrentLineNumber() : bi.getLineNumber();
                if (lineNum == null || lineNum <= 0) continue;

                int newLine = mapLineNumber(lineNum, hunks, fileDiff);
                if (newLine != lineNum) {
                    bi.setCurrentLineNumber(newLine);
                    branchIssueRepository.save(bi);
                    updated++;
                }
            }

            if (updated > 0) {
                log.info("Reconciled line numbers for {} branch issues in {} (branch: {})",
                        updated, filePath, branch.getBranchName());
            }
        }
    }

    /**
     * Verify and correct branch issue line numbers using their persisted codeSnippet.
     * Runs after file snapshots have been updated with current content, so we can
     * hash-match each issue's snippet against the actual file to find its true position.
     *
     * This is the "ground truth" step that catches any drift the diff-based remapping
     * or IssueTracker heuristics missed. Only issues with a non-blank codeSnippet are checked.
     *
     * IMPORTANT: This method now mutates BranchIssue's own fields only.
     * CodeAnalysisIssue records remain immutable historical records.
     */
    private void verifyIssueLineNumbersWithSnippets(Set<String> changedFiles,
                                                     Project project, Branch branch) {
        for (String filePath : changedFiles) {
            // Load current file content from the latest snapshot
            Optional<String> contentOpt = fileSnapshotService.getFileContentForBranch(
                    project.getId(), branch.getBranchName(), filePath);
            if (contentOpt.isEmpty()) continue;

            LineHashSequence lineHashes = LineHashSequence.from(contentOpt.get());
            if (lineHashes.getLineCount() == 0) continue;

            List<BranchIssue> issues = branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branch.getId(), filePath)
                    .stream()
                    .filter(bi -> bi.getCodeSnippet() != null && !bi.getCodeSnippet().isBlank())
                    .toList();

            if (issues.isEmpty()) continue;

            int corrected = 0;
            for (BranchIssue bi : issues) {
                Integer currentLine = bi.getCurrentLineNumber() != null
                        ? bi.getCurrentLineNumber() : bi.getLineNumber();
                if (currentLine == null || currentLine <= 0) continue;

                String snippetHash = LineHashSequence.hashLine(bi.getCodeSnippet());

                // Check if current line already matches
                if (currentLine <= lineHashes.getLineCount()) {
                    String currentHash = lineHashes.getHashForLine(currentLine);
                    if (snippetHash.equals(currentHash)) continue; // line is correct
                }

                // Line drifted — find the real position via snippet hash
                int foundLine = lineHashes.findClosestLineForHash(snippetHash, currentLine);
                if (foundLine > 0 && foundLine != currentLine) {
                    log.info("Snippet verification corrected branch issue {} in {}:{} -> {} (snippet: \"{}\")",
                            bi.getId(), filePath, currentLine, foundLine,
                            bi.getCodeSnippet().length() > 60
                                    ? bi.getCodeSnippet().substring(0, 57) + "..." : bi.getCodeSnippet());
                    bi.setCurrentLineNumber(foundLine);
                    // Update lineHash and context to match new position
                    bi.setCurrentLineHash(lineHashes.getHashForLine(foundLine));
                    bi.setLineHashContext(lineHashes.getContextHash(foundLine, 2));
                    branchIssueRepository.save(bi);
                    corrected++;
                }
            }

            if (corrected > 0) {
                log.info("Snippet verification corrected {} branch issue line numbers in {} (branch: {})",
                        corrected, filePath, branch.getBranchName());
            }
        }
    }

    /**
     * Fetch current file content from VCS for changed files and update file snapshots
     * in the latest analysis for this branch. This ensures the source code viewer
     * shows current file content after issue line numbers are reconciled.
     */
    private void updateFileSnapshotsForBranch(Set<String> existingFiles, Project project,
                                               BranchProcessRequest request, VcsInfo vcsInfo,
                                               VcsOperationsService operationsService, OkHttpClient client) {
        if (existingFiles.isEmpty()) return;

        try {
            // Find the latest CodeAnalysis for this branch — this is what the source viewer uses
            Optional<CodeAnalysis> latestAnalysisOpt = codeAnalysisRepository
                    .findLatestByProjectIdAndBranchName(project.getId(), request.getTargetBranchName());
            if (latestAnalysisOpt.isEmpty()) {
                log.debug("No existing analysis found for branch {} — skipping snapshot update",
                        request.getTargetBranchName());
                return;
            }
            CodeAnalysis latestAnalysis = latestAnalysisOpt.get();

            // Fetch current file content for each changed file that exists in the branch
            Map<String, String> fileContents = new LinkedHashMap<>();
            for (String filePath : existingFiles) {
                try {
                    String content = operationsService.getFileContent(
                            client, vcsInfo.workspace(), vcsInfo.repoSlug(),
                            request.getCommitHash(), filePath);
                    if (content != null) {
                        fileContents.put(filePath, content);
                    }
                } catch (Exception e) {
                    log.debug("Could not fetch content for {} (snapshot update): {}", filePath, e.getMessage());
                }
            }

            if (!fileContents.isEmpty()) {
                int updated = fileSnapshotService.updateOrPersistSnapshots(
                        latestAnalysis, fileContents, request.getCommitHash());
                if (updated > 0) {
                    log.info("Updated {} file snapshots in analysis {} for branch {} (commit: {})",
                            updated, latestAnalysis.getId(), request.getTargetBranchName(),
                            request.getCommitHash().substring(0, Math.min(7, request.getCommitHash().length())));
                }
            }
        } catch (Exception e) {
            log.warn("Failed to update file snapshots for branch {} (non-critical): {}",
                    request.getTargetBranchName(), e.getMessage());
        }
    }

    /**
     * Split a multi-file unified diff into per-file sections.
     * @return map of filePath → that file's diff text (from "diff --git" to next "diff --git" or EOF)
     */
    private Map<String, String> splitDiffByFile(String rawDiff) {
        Map<String, String> result = new LinkedHashMap<>();
        String[] lines = rawDiff.split("\\r?\\n");
        String currentFile = null;
        StringBuilder currentSection = new StringBuilder();

        for (String line : lines) {
            Matcher m = DIFF_GIT_PATTERN.matcher(line);
            if (m.find()) {
                // flush previous file
                if (currentFile != null) {
                    result.put(currentFile, currentSection.toString());
                }
                currentFile = m.group(2); // b/path
                currentSection = new StringBuilder();
            }
            if (currentFile != null) {
                currentSection.append(line).append("\n");
            }
        }
        if (currentFile != null) {
            result.put(currentFile, currentSection.toString());
        }
        return result;
    }

    /**
     * Parse all hunk headers from a single-file diff section.
     * @return list of int[4]: {oldStart, oldCount, newStart, newCount}
     */
    private List<int[]> parseHunks(String fileDiff) {
        List<int[]> hunks = new ArrayList<>();
        for (String line : fileDiff.split("\\r?\\n")) {
            Matcher m = HUNK_HEADER.matcher(line);
            if (m.find()) {
                int oldStart = Integer.parseInt(m.group(1));
                int oldCount = m.group(2) != null ? Integer.parseInt(m.group(2)) : 1;
                int newStart = Integer.parseInt(m.group(3));
                int newCount = m.group(4) != null ? Integer.parseInt(m.group(4)) : 1;
                hunks.add(new int[]{oldStart, oldCount, newStart, newCount});
            }
        }
        return hunks;
    }

    /**
     * Map an old line number to its new position after the diff was applied.
     * <p>
     * Algorithm:
     * <ul>
     *   <li>If the line is <em>before</em> the first hunk → unchanged.</li>
     *   <li>If the line falls <em>between</em> two hunks → shift by the cumulative
     *       offset (sum of newCount−oldCount for all preceding hunks).</li>
     *   <li>If the line falls <em>inside</em> a hunk → walk the hunk body line-by-line
     *       to find the precise new position. If the line was deleted (only present
     *       as a '-' line), return the old line number unchanged (the AI reconciliation
     *       will decide if the issue is resolved).</li>
     * </ul>
     *
     * @param oldLine  the original 1-based line number
     * @param hunks    parsed hunk headers [{oldStart, oldCount, newStart, newCount}, ...]
     * @param fileDiff the full diff section for this file (needed for line-by-line walk)
     * @return the new 1-based line number, or the original if the line was deleted
     */
    private int mapLineNumber(int oldLine, List<int[]> hunks, String fileDiff) {
        int cumulativeOffset = 0;

        for (int[] hunk : hunks) {
            int oldStart = hunk[0];
            int oldCount = hunk[1];
            int newStart = hunk[2];
            int newCount = hunk[3];
            int oldEnd = oldStart + oldCount - 1; // inclusive

            if (oldLine < oldStart) {
                // Line is before this hunk — apply accumulated offset from previous hunks
                return oldLine + cumulativeOffset;
            }

            if (oldLine <= oldEnd) {
                // Line falls inside this hunk — walk the hunk body for precise mapping
                return mapLineInsideHunk(oldLine, oldStart, newStart, fileDiff);
            }

            // Line is after this hunk — accumulate offset and continue
            cumulativeOffset += (newCount - oldCount);
        }

        // Line is after all hunks
        return oldLine + cumulativeOffset;
    }

    /**
     * Walk a hunk body line-by-line to map an old line number to its new position.
     * Context lines (' ') and modified lines advance both old and new counters.
     * Removed lines ('-') advance only the old counter.
     * Added lines ('+') advance only the new counter.
     * <p>
     * If the target old line was deleted, returns the original line number unchanged.
     */
    private int mapLineInsideHunk(int targetOldLine, int hunkOldStart, int hunkNewStart, String fileDiff) {
        String[] lines = fileDiff.split("\\r?\\n");
        int oldCursor = hunkOldStart;
        int newCursor = hunkNewStart;
        boolean inTargetHunk = false;

        for (String line : lines) {
            // Find the right hunk
            Matcher hm = HUNK_HEADER.matcher(line);
            if (hm.find()) {
                int thisOldStart = Integer.parseInt(hm.group(1));
                if (thisOldStart == hunkOldStart) {
                    inTargetHunk = true;
                    oldCursor = hunkOldStart;
                    newCursor = hunkNewStart;
                    continue;
                } else if (inTargetHunk) {
                    break; // moved past our hunk
                }
                continue;
            }

            if (!inTargetHunk) continue;

            if (line.startsWith("-")) {
                if (oldCursor == targetOldLine) {
                    // This line was deleted — return original (AI reconciliation will handle)
                    return targetOldLine;
                }
                oldCursor++;
            } else if (line.startsWith("+")) {
                newCursor++;
            } else {
                // Context line (starts with ' ' or is the line itself)
                if (oldCursor == targetOldLine) {
                    return newCursor;
                }
                oldCursor++;
                newCursor++;
            }
        }

        // Fallback — couldn't map precisely, return original
        return targetOldLine;
    }

    private void reanalyzeCandidateIssues(Set<String> changedFiles, Set<String> filesExistingInBranch, Branch branch, Project project, BranchProcessRequest request, Consumer<Map<String, Object>> consumer) {
        List<BranchIssue> candidateBranchIssues = new ArrayList<>();
        for (String filePath : changedFiles) {
            List<BranchIssue> branchIssues = branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branch.getId(), filePath);
            candidateBranchIssues.addAll(branchIssues);
        }
        if (candidateBranchIssues.isEmpty()) {
            log.info("No pre-existing issues to re-analyze (Branch: {})",
                    request.getTargetBranchName());
            return;
        }

        // =====================================================================
        // RULE-BASED PRE-FILTER: auto-resolve issues for deleted files
        // =====================================================================
        List<BranchIssue> autoResolved = new ArrayList<>();
        List<BranchIssue> needsTracking = new ArrayList<>();

        for (BranchIssue issue : candidateBranchIssues) {
            String filePath = issue.getFilePath();

            if (filePath != null && !filesExistingInBranch.contains(filePath)) {
                autoResolved.add(issue);
            } else {
                needsTracking.add(issue);
            }
        }

        // Auto-resolve issues for deleted files
        if (!autoResolved.isEmpty()) {
            log.info("Auto-resolving {} issues for deleted files (Branch: {})",
                    autoResolved.size(), request.getTargetBranchName());
            for (BranchIssue issue : autoResolved) {
                resolveIssue(issue, request.getCommitHash(), request.getSourcePrNumber(),
                        "File deleted from branch", "file-deletion");
            }
        }

        if (needsTracking.isEmpty()) {
            log.info("All {} pre-existing issues auto-resolved via file-existence check (Branch: {})",
                    autoResolved.size(), request.getTargetBranchName());
            updateBranchCountsAfterReconciliation(branch);
            return;
        }

        // =====================================================================
        // DETERMINISTIC TRACKING: content-based issue identity
        // =====================================================================
        consumer.accept(Map.of(
                "type", "status",
                "state", "tracking_issues",
                "message", "Tracking " + needsTracking.size() + " pre-existing issues deterministically (" + autoResolved.size() + " auto-resolved)"
        ));

        VcsInfo vcsInfo = getVcsInfo(project);
        EVcsProvider provider = getVcsProvider(project);
        VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);
        OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());

        // Group issues by file for per-file tracking
        Map<String, List<BranchIssue>> issuesByFile = new LinkedHashMap<>();
        for (BranchIssue bi : needsTracking) {
            String fp = bi.getFilePath();
            if (fp != null) {
                issuesByFile.computeIfAbsent(fp, k -> new ArrayList<>()).add(bi);
            }
        }

        List<BranchIssue> deterministicallyResolved = new ArrayList<>();
        List<BranchIssue> deterministicallyConfirmed = new ArrayList<>();
        List<BranchIssue> needsAiReconciliation = new ArrayList<>();

        for (Map.Entry<String, List<BranchIssue>> entry : issuesByFile.entrySet()) {
            String filePath = entry.getKey();
            List<BranchIssue> fileIssues = entry.getValue();

            // Fetch current file content for line hashing
            LineHashSequence currentHashes;
            try {
                String fileContent = operationsService.getFileContent(
                        client, vcsInfo.workspace(), vcsInfo.repoSlug(),
                        request.getCommitHash(), filePath);
                currentHashes = fileContent != null ? LineHashSequence.from(fileContent) : LineHashSequence.empty();
            } catch (Exception e) {
                log.warn("Failed to fetch file content for {}: {}. Falling back to AI reconciliation.",
                        filePath, e.getMessage());
                needsAiReconciliation.addAll(fileIssues);
                continue;
            }

            // ── Direct content verification ──
            // For each issue, check whether its anchoring content (codeSnippet or lineHash)
            // still exists in the current file. This is a direct existence check, NOT a
            // tracker-based comparison (using the tracker to compare an issue list against
            // itself always produces false-positive matches because both sides share the
            // same fingerprint).
            for (BranchIssue bi : fileIssues) {
                Integer currentLine = bi.getCurrentLineNumber() != null
                        ? bi.getCurrentLineNumber() : bi.getLineNumber();
                boolean contentFound = false;
                String updatedLineHash = null;

                // Issues at line <= 1 with no codeSnippet have no reliable content anchor.
                // Line 1 is typically boilerplate (<?php, import, package) whose lineHash
                // is effectively constant — using it for matching always "confirms" the
                // issue, making it immortal. Send these directly to AI reconciliation.
                boolean hasNoReliableAnchor = (currentLine == null || currentLine <= 1)
                        && (bi.getCodeSnippet() == null || bi.getCodeSnippet().isBlank());
                if (hasNoReliableAnchor) {
                    needsAiReconciliation.add(bi);
                    continue;
                }

                // 1st priority: use persisted codeSnippet for content-based anchoring
                // (strongest signal — the exact source line the issue references)
                if (bi.getCodeSnippet() != null && !bi.getCodeSnippet().isBlank()
                        && currentHashes.getLineCount() > 0) {
                    String snippetHash = LineHashSequence.hashLine(bi.getCodeSnippet());
                    int foundLine = currentHashes.findClosestLineForHash(
                            snippetHash, currentLine != null ? currentLine : 1);
                    if (foundLine > 0) {
                        currentLine = foundLine;
                        updatedLineHash = currentHashes.getHashForLine(foundLine);
                        contentFound = true;
                    }
                }

                // 2nd priority: fall back to lineHash lookup
                if (!contentFound && bi.getLineHash() != null
                        && currentHashes.getLineCount() > 0) {
                    int foundLine = currentHashes.findClosestLineForHash(
                            bi.getLineHash(), currentLine != null ? currentLine : 1);
                    if (foundLine > 0) {
                        currentLine = foundLine;
                        updatedLineHash = bi.getLineHash(); // Hash matches, content identical
                        contentFound = true;
                    }
                }

                if (contentFound) {
                    // Issue's actual content still exists in the file — confirmed
                    bi.setCurrentLineNumber(currentLine);
                    bi.setCurrentLineHash(updatedLineHash);
                    bi.setLastVerifiedCommit(request.getCommitHash());
                    bi.setTrackingConfidence(TrackingConfidence.EXACT);
                    branchIssueRepository.save(bi);
                    deterministicallyConfirmed.add(bi);
                } else if (bi.getCodeSnippet() != null && !bi.getCodeSnippet().isBlank()) {
                    // Had a codeSnippet anchor but it's no longer found anywhere in the file.
                    // The code was fixed/removed — resolve deterministically.
                    deterministicallyResolved.add(bi);
                } else if (bi.getLineHash() != null) {
                    // Had a lineHash anchor but it's no longer found in the file.
                    // The line content changed — resolve deterministically.
                    deterministicallyResolved.add(bi);
                } else {
                    // No content anchors at all — can't determine, send to AI
                    needsAiReconciliation.add(bi);
                }
            }
        }

        // Resolve deterministically-resolved issues (BranchIssue only, CodeAnalysisIssue untouched)
        if (!deterministicallyResolved.isEmpty()) {
            log.info("Deterministically resolving {} issues (no longer match current code) (Branch: {})",
                    deterministicallyResolved.size(), request.getTargetBranchName());
            for (BranchIssue bi : deterministicallyResolved) {
                resolveIssue(bi, request.getCommitHash(), request.getSourcePrNumber(),
                        "Issue no longer matches current code (deterministic tracking)", "deterministic-tracking");
            }
        }

        log.info("Deterministic tracking results: {} confirmed, {} resolved, {} need AI (Branch: {})",
                deterministicallyConfirmed.size(), deterministicallyResolved.size(),
                needsAiReconciliation.size(), request.getTargetBranchName());

        // =====================================================================
        // AI FALLBACK: only for issues without tracking data (<10% expected)
        // =====================================================================
        if (!needsAiReconciliation.isEmpty()) {
            consumer.accept(Map.of(
                    "type", "status",
                    "state", "reanalyzing_issues",
                    "message", "AI reconciliation for " + needsAiReconciliation.size() + " ambiguous issues"
            ));

            try {
                // Build DTOs directly from BranchIssue's own fields — no lazy
                // CodeAnalysisIssue proxy dereference required.  This avoids the
                // LazyInitializationException that occurred when the transient
                // CodeAnalysis.setIssues() triggered updateIssueCounts() on
                // detached proxy objects.
                List<AiRequestPreviousIssueDTO> previousIssueDTOs = needsAiReconciliation.stream()
                        .map(AiRequestPreviousIssueDTO::fromBranchIssue)
                        .toList();

                VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);

                AiAnalysisRequest aiReq = aiClientService.buildAiAnalysisRequestForBranchReconciliation(
                        project,
                        request,
                        previousIssueDTOs
                );

                Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiReq, event -> {
                    try {
                        consumer.accept(event);
                    } catch (Exception ex) {
                        log.warn("Event consumer failed: {}", ex.getMessage());
                    }
                });

                Object issuesObj = aiResponse.get("issues");

                if (issuesObj instanceof List) {
                    @SuppressWarnings("unchecked")
                    List<Object> issuesList = (List<Object>) issuesObj;
                    for (Object item : issuesList) {
                        if (item instanceof Map) {
                            @SuppressWarnings("unchecked")
                            Map<String, Object> issueData = (Map<String, Object>) item;
                            processReconciledIssue(issueData, branch, request.getCommitHash(), request.getSourcePrNumber());
                        }
                    }
                } else if (issuesObj instanceof Map) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> issuesMap = (Map<String, Object>) issuesObj;
                    for (Map.Entry<String, Object> mapEntry : issuesMap.entrySet()) {
                        Object val = mapEntry.getValue();
                        if (val instanceof Map) {
                            @SuppressWarnings("unchecked")
                            Map<String, Object> issueData = (Map<String, Object>) val;
                            processReconciledIssue(issueData, branch, request.getCommitHash(), request.getSourcePrNumber());
                        }
                    }
                } else if (issuesObj != null) {
                    log.warn("Issues field is neither List nor Map: {}", issuesObj.getClass().getName());
                }

                if (project.getDefaultBranch() == null) {
                    project.setDefaultBranch(branch);
                }
            } catch (Exception ex) {
                log.warn("AI reconciliation failed (Branch: {}): {}",
                        request.getTargetBranchName(), ex.getMessage(), ex);
            }
        }

        updateBranchCountsAfterReconciliation(branch);
    }

    /**
     * Resolve a BranchIssue. The underlying CodeAnalysisIssue is NOT mutated —
     * it remains an immutable historical record of the issue as detected.
     * Branch-level resolution is the only thing that matters for branch views.
     */
    private void resolveIssue(BranchIssue bi, String commitHash, Long prNumber,
                              String description, String resolvedBy) {
        java.time.OffsetDateTime now = java.time.OffsetDateTime.now();

        bi.setResolved(true);
        bi.setResolvedAt(now);
        bi.setResolvedInCommitHash(commitHash);
        bi.setResolvedDescription(description);
        bi.setResolvedBy(resolvedBy);
        if (prNumber != null) {
            bi.setResolvedInPrNumber(prNumber);
        }
        branchIssueRepository.save(bi);

        // CodeAnalysisIssue is intentionally NOT mutated.
        // PR issues are immutable historical records.
    }

    /**
     * Refresh branch from DB and update issue counts after any reconciliation step.
     */
    private void updateBranchCountsAfterReconciliation(Branch branch) {
        Branch refreshedBranch = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
        refreshedBranch.updateIssueCounts();
        branchRepository.save(refreshedBranch);
        log.info("Updated branch issue counts after reconciliation: total={}, high={}, medium={}, low={}, resolved={}",
                refreshedBranch.getTotalIssues(), refreshedBranch.getHighSeverityCount(),
                refreshedBranch.getMediumSeverityCount(), refreshedBranch.getLowSeverityCount(),
                refreshedBranch.getResolvedCount());
    }

    private void processReconciledIssue(Map<String, Object> issueData, Branch branch, String commitHash, Long sourcePrNumber) {
        // Try both "issueId" (as instructed in prompt) and "id" (fallback) for the issue identifier
        // The AI returns the CodeAnalysisIssue ID — we look up the BranchIssue via origin_issue_id
        Object issueIdFromAi = issueData.get("issueId");
        if (issueIdFromAi == null) {
            issueIdFromAi = issueData.get("id");
        }
        Long actualIssueId = null;

        if (issueIdFromAi != null) {
            try {
                actualIssueId = Long.parseLong(String.valueOf(issueIdFromAi));
            } catch (NumberFormatException e) {
                log.warn("Invalid issueId in AI response: {}", issueIdFromAi);
            }
        }

        Object isResolvedObj = issueData.get("isResolved");
        boolean resolved = false;
        if (isResolvedObj instanceof Boolean) {
            resolved = (Boolean) isResolvedObj;
        } else if (issueData.get("status") != null &&
                "resolved".equalsIgnoreCase(String.valueOf(issueData.get("status")))) {
            resolved = true;
        }

        // Extract AI's resolution reason/description if provided
        String resolvedDescription = null;
        if (issueData.get("reason") != null) {
            resolvedDescription = String.valueOf(issueData.get("reason"));
        }

        if (resolved && actualIssueId != null) {
            // Look up BranchIssue by origin issue ID (the AI returns the CodeAnalysisIssue ID)
            Optional<BranchIssue> branchIssueOpt = branchIssueRepository
                    .findByBranchIdAndOriginIssueId(branch.getId(), actualIssueId);
            if (branchIssueOpt.isPresent()) {
                BranchIssue bi = branchIssueOpt.get();
                if (!bi.isResolved()) {
                    java.time.OffsetDateTime now = java.time.OffsetDateTime.now();
                    
                    // Update BranchIssue with resolution info — ONLY BranchIssue is mutated
                    bi.setResolved(true);
                    bi.setResolvedInPrNumber(sourcePrNumber);
                    bi.setResolvedInCommitHash(commitHash);
                    bi.setResolvedDescription(resolvedDescription);
                    bi.setResolvedAt(now);
                    bi.setResolvedBy("AI-reconciliation");
                    branchIssueRepository.save(bi);

                    // CodeAnalysisIssue is intentionally NOT mutated.
                    // PR issues are immutable historical records.

                    log.info("Marked branch issue {} (origin CAI: {}) as resolved (commit: {}, PR: {}, description: {})",
                            bi.getId(),
                            actualIssueId,
                            commitHash,
                            sourcePrNumber,
                            resolvedDescription != null ? resolvedDescription.substring(0, Math.min(100, resolvedDescription.length())) : "none");
                }
            } else {
                log.debug("No BranchIssue found for origin issue {} in branch {} — may have been created before migration",
                        actualIssueId, branch.getId());
            }
        }
    }

    private void performIncrementalRagUpdate(
            BranchProcessRequest request,
            Project project,
            VcsInfo vcsInfo,
            String commitDiff,  // Note: This is commit diff, but we need branch diff for non-main branches
            Consumer<Map<String, Object>> consumer
    ) {
        // Skip if RAG operations service is not available
        if (ragOperationsService == null) {
            log.info("Skipping RAG incremental update - RagOperationsService not available (bean not injected)");
            return;
        }

        try {
            // Check if RAG is enabled for this project
            if (!ragOperationsService.isRagEnabled(project)) {
                log.info("Skipping RAG incremental update - RAG not enabled for project={}", project.getId());
                return;
            }

            if (!ragOperationsService.isRagIndexReady(project)) {
                log.info("Skipping RAG incremental update - RAG index not yet ready for project={}", project.getId());
                return;
            }

            String targetBranch = request.getTargetBranchName();
            String commit = request.getCommitHash();
            
            // Get base branch to determine if this is main branch or feature branch
            String baseBranch = ragOperationsService.getBaseBranch(project);
            
            if (targetBranch.equals(baseBranch)) {
                // Main branch push - use commit diff for incremental update
                log.info("Main branch push - updating RAG index with commit diff for project={}, branch={}, commit={}",
                        project.getId(), targetBranch, commit);
                
                consumer.accept(Map.of(
                        "type", "status",
                        "state", "rag_update",
                        "message", "Updating RAG index with changed files"
                ));
                
                ragOperationsService.triggerIncrementalUpdate(project, targetBranch, commit, commitDiff, consumer);
            } else {
                // Non-main branch push - update branch index (calculates full diff vs base branch)
                log.info("Non-main branch push - updating branch index for project={}, branch={}", 
                        project.getId(), targetBranch);
                
                ragOperationsService.updateBranchIndex(project, targetBranch, consumer);
            }

            log.info("RAG update completed for project={}, branch={}, commit={}",
                    project.getId(), targetBranch, commit);

        } catch (Exception e) {
            log.warn("RAG incremental update failed (non-critical): {}", e.getMessage());
            consumer.accept(Map.of(
                    "type", "warning",
                    "state", "rag_error",
                    "message", "RAG incremental update failed (non-critical): " + e.getMessage()
            ));
        }
    }
}
