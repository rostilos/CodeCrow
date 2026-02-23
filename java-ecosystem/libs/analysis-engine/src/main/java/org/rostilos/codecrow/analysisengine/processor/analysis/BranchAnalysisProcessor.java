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
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchFileRepository;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
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
                    unanalyzedCommits = gitGraphSyncService.findUnanalyzedCommitRange(
                            nodeMap, request.getCommitHash());
                    
                    if (unanalyzedCommits.isEmpty()) {
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
                    log.info("Fetched DAG-based diff ({}..{}) — covers {} unanalyzed commits",
                            dagDiffBase.substring(0, Math.min(7, dagDiffBase.length())),
                            request.getCommitHash().substring(0, Math.min(7, request.getCommitHash().length())),
                            unanalyzedCommits.size());
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
                    log.info("Fetched delta diff ({}..{}) for branch analysis — captures all changes since last success",
                            lastSuccessfulCommit.substring(0, Math.min(7, lastSuccessfulCommit.length())),
                            request.getCommitHash().substring(0, Math.min(7, request.getCommitHash().length())));
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

            // Reconcile line numbers BEFORE counting or dedup — walk the diff hunks
            // and update each existing issue's lineNumber to its new position.
            reconcileIssueLineNumbers(rawDiff, changedFiles, project, request.getTargetBranchName());

            Set<String> existingFiles = updateBranchFiles(changedFiles, project, request.getTargetBranchName());
            Branch branch = createOrUpdateProjectBranch(project, request, existingBranchOpt.orElse(null));

            mapCodeAnalysisIssuesToBranch(changedFiles, existingFiles, branch, project);
            
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
     * @return the set of file paths confirmed to exist in the branch (used to avoid redundant API calls)
     */
    private Set<String> updateBranchFiles(Set<String> changedFiles, Project project, String branchName) {
        VcsInfo vcsInfo = getVcsInfo(project);
        EVcsProvider provider = getVcsProvider(project);
        VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);
        Set<String> filesExistingInBranch = new HashSet<>();

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
                // On error, assume the file exists so we don't skip it
                filesExistingInBranch.add(filePath);
            }

            List<CodeAnalysisIssue> relatedIssues = codeAnalysisIssueRepository
                    .findByProjectIdAndFilePath(project.getId(), filePath);
            List<CodeAnalysisIssue> branchSpecific = relatedIssues.stream()
                    .filter(issue -> branchName.equals(issue.getAnalysis().getBranchName()) ||
                            branchName.equals(issue.getAnalysis().getSourceBranchName()))
                    .toList();

            // Deduplicate by content key before counting — multiple analyses may
            // report the same logical issue with different DB ids.
            // Line numbers are kept accurate by reconcileIssueLineNumbers() which
            // runs before this method.
            //
            // Three-tier dedup:
            //   Tier 1: exact content key (filePath:lineNumber:category — no severity)
            //   Tier 2: whole-file dedup — line ≤ 1 means "whole file" and matches
            //           any other line for the same filePath:category combination.
            Set<String> seenContentKeys = new HashSet<>();
            Set<String> seenFileCatKeys = new HashSet<>();
            Set<String> seenWholeFileCatKeys = new HashSet<>();
            long unresolvedCount = 0;
            for (CodeAnalysisIssue i : branchSpecific) {
                if (i.isResolved()) continue;
                String contentKey = buildIssueContentKey(i);
                String fileCatKey = buildIssueFileKey(i);
                boolean wholeFile = isWholeFileLine(i.getLineNumber());

                if (!seenContentKeys.add(contentKey)) continue;           // exact duplicate
                if (seenWholeFileCatKeys.contains(fileCatKey)) continue;  // existing whole-file covers this
                if (wholeFile && seenFileCatKeys.contains(fileCatKey)) continue; // new whole-file, specific already exists

                seenFileCatKeys.add(fileCatKey);
                if (wholeFile) seenWholeFileCatKeys.add(fileCatKey);
                unresolvedCount++;
            }

            Optional<BranchFile> projectFileOptional = branchFileRepository
                    .findByProjectIdAndBranchNameAndFilePath(project.getId(), branchName, filePath);
            if (projectFileOptional.isPresent()) {
                BranchFile branchFile = projectFileOptional.get();
                if (branchFile.getIssueCount() != (int) unresolvedCount) {
                    branchFile.setIssueCount((int) unresolvedCount);
                    branchFileRepository.save(branchFile);
                }
            } else {
                BranchFile branchFile = new BranchFile();
                branchFile.setProject(project);
                branchFile.setBranchName(branchName);
                branchFile.setFilePath(filePath);
                branchFile.setIssueCount((int) unresolvedCount);
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

            // Content-based deduplication: build maps of existing BranchIssues to
            // prevent the same logical issue from being linked multiple times across analyses.
            //
            // Three-tier dedup (same logic as updateBranchFiles counting):
            //   Tier 1: exact ID match — same CodeAnalysisIssue already linked
            //   Tier 2: exact content key (filePath:lineNumber:category — no severity)
            //   Tier 3: whole-file dedup — line ≤ 1 means "whole file" and matches
            //           any other line for the same filePath:category combination.
            //           This catches the AI generating the same finding at both line 1
            //           ("whole file") and a specific line across consecutive analyses.
            List<BranchIssue> existingBranchIssues = branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branch.getId(), filePath);
            Map<String, BranchIssue> contentKeyMap = new HashMap<>();
            Set<String> fileCatKeys = new HashSet<>();
            Set<String> wholeFileCatKeys = new HashSet<>();
            for (BranchIssue bi : existingBranchIssues) {
                CodeAnalysisIssue biIssue = bi.getCodeAnalysisIssue();
                contentKeyMap.putIfAbsent(buildIssueContentKey(biIssue), bi);
                fileCatKeys.add(buildIssueFileKey(biIssue));
                if (isWholeFileLine(biIssue.getLineNumber())) {
                    wholeFileCatKeys.add(buildIssueFileKey(biIssue));
                }
            }

            int skipped = 0;
            for (CodeAnalysisIssue issue : branchSpecificIssues) {
                // Tier 1: exact ID match — same CodeAnalysisIssue already linked
                Optional<BranchIssue> existing = branchIssueRepository
                        .findByBranchIdAndCodeAnalysisIssueId(branch.getId(), issue.getId());

                if (existing.isPresent()) {
                    BranchIssue bc = existing.get();
                    bc.setSeverity(issue.getSeverity());
                    branchIssueRepository.saveAndFlush(bc);
                    continue;
                }

                String contentKey = buildIssueContentKey(issue);
                String fileCatKey = buildIssueFileKey(issue);
                boolean wholeFile = isWholeFileLine(issue.getLineNumber());

                // Tier 2: exact content key match (filePath:lineNumber:category)
                if (contentKeyMap.containsKey(contentKey)) {
                    skipped++;
                    continue;
                }

                // Tier 3: whole-file dedup
                // If an existing issue at line ≤ 1 covers this file+category → duplicate
                if (wholeFileCatKeys.contains(fileCatKey)) {
                    skipped++;
                    continue;
                }
                // If this issue is at line ≤ 1 and ANY issue already exists for file+category → duplicate
                if (wholeFile && fileCatKeys.contains(fileCatKey)) {
                    skipped++;
                    continue;
                }

                // No match — create new BranchIssue
                BranchIssue bc = new BranchIssue();
                bc.setBranch(branch);
                bc.setCodeAnalysisIssue(issue);
                bc.setResolved(issue.isResolved());
                bc.setSeverity(issue.getSeverity());
                bc.setFirstDetectedPrNumber(issue.getAnalysis() != null ? issue.getAnalysis().getPrNumber() : null);
                branchIssueRepository.saveAndFlush(bc);
                // Register in all maps so subsequent issues in this batch also dedup
                contentKeyMap.put(contentKey, bc);
                fileCatKeys.add(fileCatKey);
                if (wholeFile) wholeFileCatKeys.add(fileCatKey);
            }

            if (skipped > 0) {
                log.debug("Skipped {} duplicate issue(s) for file {} in branch {}",
                        skipped, filePath, branch.getBranchName());
            }
        }
    }

    /**
     * Builds a content key for deduplication of branch issues.
     * Two CodeAnalysisIssue records with the same key represent the same logical issue.
     * <p>
     * Key = filePath:lineNumber:category (severity intentionally excluded —
     * the AI often assigns different severities to the same finding across runs).
     * <p>
     * Line numbers are reliable here because {@link #reconcileIssueLineNumbers}
     * updates them to their current positions (via diff hunk mapping) before
     * this method is ever called.
     */
    private String buildIssueContentKey(CodeAnalysisIssue issue) {
        return issue.getFilePath() + ":" +
               issue.getLineNumber() + ":" +
               issue.getIssueCategory();
    }

    /**
     * Builds a file-level key for whole-file deduplication.
     * Key = filePath:category — used to catch the common AI pattern of reporting
     * the same finding at both line 1 ("whole file") and a specific line number.
     */
    private String buildIssueFileKey(CodeAnalysisIssue issue) {
        return issue.getFilePath() + ":" + issue.getIssueCategory();
    }

    /**
     * Returns true if the line number represents a "whole file" reference
     * rather than a specific code line. The AI defaults to line 0 or 1 when
     * it cannot pinpoint the exact location, meaning the finding applies to
     * the entire file.
     */
    private boolean isWholeFileLine(Integer lineNumber) {
        return lineNumber == null || lineNumber <= 1;
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
     * {@code lineNumber} of each unresolved {@link CodeAnalysisIssue} so that
     * it reflects the line's position <em>after</em> the diff was applied.
     * <p>
     * If an issue's line was deleted (no corresponding new line), we leave the
     * line number unchanged — the AI reconciliation step will handle resolution.
     *
     * @param rawDiff       full unified diff (may contain multiple files)
     * @param changedFiles  set of file paths that changed
     * @param project       current project
     * @param branchName    target branch name
     */
    private void reconcileIssueLineNumbers(String rawDiff, Set<String> changedFiles,
                                           Project project, String branchName) {
        if (rawDiff == null || rawDiff.isBlank()) return;

        Map<String, String> perFileDiffs = splitDiffByFile(rawDiff);

        for (String filePath : changedFiles) {
            String fileDiff = perFileDiffs.get(filePath);
            if (fileDiff == null) continue; // file mentioned but no actual hunks (e.g. binary)

            List<CodeAnalysisIssue> issues = codeAnalysisIssueRepository
                    .findByProjectIdAndFilePath(project.getId(), filePath)
                    .stream()
                    .filter(i -> !i.isResolved())
                    .filter(i -> {
                        CodeAnalysis a = i.getAnalysis();
                        if (a == null) return false;
                        return branchName.equals(a.getBranchName()) ||
                               branchName.equals(a.getSourceBranchName());
                    })
                    .toList();

            if (issues.isEmpty()) continue;

            List<int[]> hunks = parseHunks(fileDiff);
            if (hunks.isEmpty()) continue;

            int updated = 0;
            for (CodeAnalysisIssue issue : issues) {
                if (issue.getLineNumber() == null || issue.getLineNumber() <= 0) continue;

                int newLine = mapLineNumber(issue.getLineNumber(), hunks, fileDiff);
                if (newLine != issue.getLineNumber()) {
                    issue.setLineNumber(newLine);
                    codeAnalysisIssueRepository.save(issue);
                    updated++;
                }
            }

            if (updated > 0) {
                log.info("Reconciled line numbers for {} issues in {} (branch: {})",
                        updated, filePath, branchName);
            }
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
        if (!candidateBranchIssues.isEmpty()) {
            // =====================================================================
            // RULE-BASED PRE-FILTER: auto-resolve issues for deleted files
            // =====================================================================
            // If the file no longer exists in the branch, the issue is definitively
            // resolved — no need to waste an AI call for this. Only issues in files
            // that still exist are forwarded to the AI for nuanced reconciliation.
            //
            // NOTE: We intentionally skip line-number matching here. The AI can
            // report inaccurate line numbers (0, 1, or off-by-N), so line-based
            // rules would produce false-positives. File-existence is a safe,
            // deterministic signal.
            // =====================================================================
            List<BranchIssue> autoResolved = new ArrayList<>();
            List<BranchIssue> needsAiReconciliation = new ArrayList<>();
            
            for (BranchIssue issue : candidateBranchIssues) {
                String filePath = issue.getCodeAnalysisIssue() != null 
                        ? issue.getCodeAnalysisIssue().getFilePath() 
                        : null;
                
                if (filePath != null && !filesExistingInBranch.contains(filePath)) {
                    // File was deleted from the branch — auto-resolve
                    autoResolved.add(issue);
                } else {
                    // File still exists — needs AI to determine if issue persists
                    needsAiReconciliation.add(issue);
                }
            }
            
            // Auto-resolve issues for deleted files
            if (!autoResolved.isEmpty()) {
                log.info("Auto-resolving {} issues for deleted files (Branch: {})",
                        autoResolved.size(), request.getTargetBranchName());
                for (BranchIssue issue : autoResolved) {
                    issue.setResolved(true);
                    issue.setResolvedAt(java.time.OffsetDateTime.now());
                    issue.setResolvedInCommitHash(request.getCommitHash());
                    if (request.getSourcePrNumber() != null) {
                        issue.setResolvedInPrNumber(request.getSourcePrNumber());
                    }
                    branchIssueRepository.save(issue);
                }
            }
            
            // Only send remaining ambiguous issues to AI
            if (!needsAiReconciliation.isEmpty()) {
                log.info("Re-analyzing {} pre-existing issues via AI, {} auto-resolved (Branch: {})",
                        needsAiReconciliation.size(), autoResolved.size(),
                        request.getTargetBranchName());

                consumer.accept(Map.of(
                        "type", "status",
                        "state", "reanalyzing_issues",
                        "message", "Re-analyzing " + needsAiReconciliation.size() + " pre-existing issues (" + autoResolved.size() + " auto-resolved)"
                ));

                try {
                    List<CodeAnalysisIssue> candidateIssues = needsAiReconciliation.stream()
                            .map(BranchIssue::getCodeAnalysisIssue)
                            .toList();

                CodeAnalysis tempAnalysis = new CodeAnalysis();
                tempAnalysis.setProject(project);
                tempAnalysis.setAnalysisType(AnalysisType.BRANCH_ANALYSIS);
                tempAnalysis.setPrNumber(null);
                tempAnalysis.setCommitHash(request.getCommitHash());
                tempAnalysis.setBranchName(request.getTargetBranchName());
                tempAnalysis.setIssues(candidateIssues);

                EVcsProvider provider = getVcsProvider(project);
                VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);

                AiAnalysisRequest aiReq = aiClientService.buildAiAnalysisRequest(
                        project,
                        request,
                        Optional.of(tempAnalysis)
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
                }
                else if (issuesObj instanceof Map) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> issuesMap = (Map<String, Object>) issuesObj;
                    for (Map.Entry<String, Object> entry : issuesMap.entrySet()) {
                        Object val = entry.getValue();
                        if (val instanceof Map) {
                            @SuppressWarnings("unchecked")
                            Map<String, Object> issueData = (Map<String, Object>) val;
                            processReconciledIssue(issueData, branch, request.getCommitHash(), request.getSourcePrNumber());
                        }
                    }
                } else if (issuesObj != null) {
                    log.warn("Issues field is neither List nor Map: {}", issuesObj.getClass().getName());
                }

                Branch refreshedBranch = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
                refreshedBranch.updateIssueCounts();
                branchRepository.save(refreshedBranch);
                log.info("Updated branch issue counts after reconciliation: total={}, high={}, medium={}, low={}, resolved={}",
                        refreshedBranch.getTotalIssues(), refreshedBranch.getHighSeverityCount(), 
                        refreshedBranch.getMediumSeverityCount(), refreshedBranch.getLowSeverityCount(), 
                        refreshedBranch.getResolvedCount());
                
                if(project.getDefaultBranch() == null) {
                    project.setDefaultBranch(refreshedBranch);
                }
            } catch (Exception ex) {
                log.warn("Targeted AI re-analysis failed (Branch: {}): {}",
                        request.getTargetBranchName(),
                        ex.getMessage(), ex);
            }
            } else {
                log.info("All {} pre-existing issues auto-resolved via file-existence check (Branch: {})",
                        autoResolved.size(), request.getTargetBranchName());
            }
            
            // Update branch issue counts after any resolution (auto or AI)
            if (!autoResolved.isEmpty()) {
                Branch refreshedAfterAutoResolve = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
                refreshedAfterAutoResolve.updateIssueCounts();
                branchRepository.save(refreshedAfterAutoResolve);
            }
        } else {
            log.info("No pre-existing issues to re-analyze (Branch: {})",
                    request.getTargetBranchName());
        }
    }

    private void processReconciledIssue(Map<String, Object> issueData, Branch branch, String commitHash, Long sourcePrNumber) {
        // Try both "issueId" (as instructed in prompt) and "id" (fallback) for the issue identifier
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
            Optional<BranchIssue> branchIssueOpt = branchIssueRepository
                    .findByBranchIdAndCodeAnalysisIssueId(branch.getId(), actualIssueId);
            if (branchIssueOpt.isPresent()) {
                BranchIssue bi = branchIssueOpt.get();
                if (!bi.isResolved()) {
                    java.time.OffsetDateTime now = java.time.OffsetDateTime.now();
                    
                    // Update BranchIssue with resolution info
                    bi.setResolved(true);
                    bi.setResolvedInPrNumber(sourcePrNumber);
                    bi.setResolvedInCommitHash(commitHash);
                    bi.setResolvedDescription(resolvedDescription);
                    bi.setResolvedAt(now);
                    bi.setResolvedBy("AI-reconciliation");
                    branchIssueRepository.save(bi);

                    // Update CodeAnalysisIssue with resolution info (preserving original issue data)
                    Optional<CodeAnalysisIssue> caiOpt = codeAnalysisIssueRepository.findById(actualIssueId);
                    if (caiOpt.isPresent()) {
                        CodeAnalysisIssue cai = caiOpt.get();
                        cai.setResolved(true);
                        cai.setResolvedDescription(resolvedDescription);
                        cai.setResolvedByPr(sourcePrNumber);
                        cai.setResolvedCommitHash(commitHash);
                        cai.setResolvedAt(now);
                        cai.setResolvedBy("AI-reconciliation");
                        // Note: original issue fields (reason, suggestedFixDescription, suggestedFixDiff, etc.) are preserved
                        codeAnalysisIssueRepository.save(cai);
                    }
                    log.info("Marked branch issue {} as resolved (commit: {}, PR: {}, description: {})",
                            actualIssueId,
                            commitHash,
                            sourcePrNumber,
                            resolvedDescription != null ? resolvedDescription.substring(0, Math.min(100, resolvedDescription.length())) : "none");
                }
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
