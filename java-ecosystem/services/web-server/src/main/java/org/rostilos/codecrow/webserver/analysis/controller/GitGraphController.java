package org.rostilos.codecrow.webserver.analysis.controller;

import org.rostilos.codecrow.commitgraph.persistence.AnalyzedCommitRepository;
import org.rostilos.codecrow.commitgraph.model.AnalyzedCommit;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;

import java.util.*;
import java.util.stream.Collectors;

/**
 * Returns git commit graph data for a project.
 * <p>
 * Fetches real-time commit history from the VCS provider API and enriches
 * it with analysis status from the {@code analyzed_commit} table.
 * <p>
 * This replaces the old approach of maintaining a full DAG in the database,
 * which was fragile and drifted from reality on merges/rebases/force-pushes.
 */
@RestController
@RequestMapping("/api/v1/projects/{projectId}/git-graph")
public class GitGraphController {

    private static final Logger log = LoggerFactory.getLogger(GitGraphController.class);
    private static final int DEFAULT_COMMIT_LIMIT = 100;

    private final BranchRepository branchRepository;
    private final PullRequestRepository pullRequestRepository;
    private final AnalyzedCommitRepository analyzedCommitRepository;
    private final CodeAnalysisRepository codeAnalysisRepository;
    private final org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository projectRepository;
    private final VcsClientProvider vcsClientProvider;

    public GitGraphController(BranchRepository branchRepository,
            PullRequestRepository pullRequestRepository,
            AnalyzedCommitRepository analyzedCommitRepository,
            CodeAnalysisRepository codeAnalysisRepository,
            org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository projectRepository,
            VcsClientProvider vcsClientProvider) {
        this.branchRepository = branchRepository;
        this.pullRequestRepository = pullRequestRepository;
        this.analyzedCommitRepository = analyzedCommitRepository;
        this.codeAnalysisRepository = codeAnalysisRepository;
        this.projectRepository = projectRepository;
        this.vcsClientProvider = vcsClientProvider;
    }

    @GetMapping
    @Transactional(readOnly = true)
    public ResponseEntity<Map<String, Object>> getGitGraph(
            @PathVariable Long projectId,
            @RequestParam(required = false) String branch) {

        Project project = projectRepository.findById(projectId).orElse(null);
        if (project == null || project.getEffectiveVcsRepoInfo() == null) {
            return ResponseEntity.ok(Map.of("commits", List.of(), "branches", List.of()));
        }

        // 1. Build the set of analyzed commit hashes for quick lookup
        List<AnalyzedCommit> analyzedCommits = analyzedCommitRepository.findByProjectId(projectId);
        Map<String, AnalyzedCommit> analyzedMap = analyzedCommits.stream()
                .collect(Collectors.toMap(AnalyzedCommit::getCommitHash, ac -> ac, (a, b) -> a));

        // 2. Build PR lookup maps: prNumber→PullRequest.id
        List<PullRequest> pullRequests = pullRequestRepository.findByProject_Id(projectId);
        Map<Long, Long> prNumberToInternalId = new HashMap<>();
        for (PullRequest pr : pullRequests) {
            prNumberToInternalId.put(pr.getPrNumber(), pr.getId());
        }

        // 3. Build branch health info from DB (cheap — no VCS API call)
        List<Branch> dbBranches = branchRepository.findByProjectId(projectId);
        Map<String, Branch> dbBranchMap = new HashMap<>();
        for (Branch b : dbBranches) {
            dbBranchMap.put(b.getBranchName(), b);
        }

        // 4. Determine which branches to fetch commits for (1-2 max, not all)
        String defaultBranchName = project.getDefaultBranch() != null
                ? project.getDefaultBranch().getBranchName() : null;

        // Build the small set of branches we actually need commit history for
        Set<String> branchesToFetch = new LinkedHashSet<>();
        if (branch != null && !branch.isBlank()) {
            branchesToFetch.add(branch.trim());
        }
        // Always include the default branch for merge-base context
        if (defaultBranchName != null) {
            branchesToFetch.add(defaultBranchName);
        }
        // Fallback: if nothing was requested and no default exists, pick first DB branch
        if (branchesToFetch.isEmpty() && !dbBranches.isEmpty()) {
            branchesToFetch.add(dbBranches.get(0).getBranchName());
        }

        // 5. Fetch commit history from VCS API — only for the targeted branches
        List<Map<String, Object>> commits = new ArrayList<>();
        Set<String> seenHashes = new HashSet<>();
        List<Map<String, Object>> branchList = new ArrayList<>();
        Set<String> branchNames = new LinkedHashSet<>();

        try {
            VcsClient vcsClient = vcsClientProvider.getClient(project.getEffectiveVcsConnection());
            String ws = project.getEffectiveVcsRepoInfo().getRepoWorkspace();
            String slug = project.getEffectiveVcsRepoInfo().getRepoSlug();

            for (String branchName : branchesToFetch) {
                // Build branch info DTO from DB (no VCS call needed)
                Branch dbBranch = dbBranchMap.get(branchName);
                Map<String, Object> branchInfo = new LinkedHashMap<>();
                branchInfo.put("name", branchName);
                if (dbBranch != null) {
                    branchInfo.put("headCommit", dbBranch.getCommitHash());
                    branchInfo.put("healthStatus", dbBranch.getHealthStatus() != null ? dbBranch.getHealthStatus().name() : null);
                    branchInfo.put("totalIssues", dbBranch.getTotalIssues());
                    branchInfo.put("highSeverity", dbBranch.getHighSeverityCount());
                    branchInfo.put("mediumSeverity", dbBranch.getMediumSeverityCount());
                    branchInfo.put("lowSeverity", dbBranch.getLowSeverityCount());
                } else {
                    branchInfo.put("healthStatus", null);
                    branchInfo.put("totalIssues", 0);
                    branchInfo.put("highSeverity", 0);
                    branchInfo.put("mediumSeverity", 0);
                    branchInfo.put("lowSeverity", 0);
                }

                try {
                    List<VcsCommit> vcsCommits = vcsClient.getCommitHistory(
                            ws, slug, branchName, DEFAULT_COMMIT_LIMIT);

                    // Set the real head commit from VCS
                    if (!vcsCommits.isEmpty()) {
                        branchInfo.put("headCommit", vcsCommits.get(0).hash());
                    }

                    for (VcsCommit vc : vcsCommits) {
                        if (seenHashes.contains(vc.hash())) continue;
                        seenHashes.add(vc.hash());

                        Map<String, Object> c = new LinkedHashMap<>();
                        c.put("hash", vc.hash());
                        c.put("message", vc.message());
                        c.put("author", vc.authorName());
                        c.put("timestamp", vc.timestamp());
                        c.put("parents", vc.parentHashes() != null ? vc.parentHashes() : Collections.emptyList());

                        // Enrich with analysis status from analyzed_commit table
                        AnalyzedCommit ac = analyzedMap.get(vc.hash());
                        if (ac != null) {
                            c.put("analysisStatus", "ANALYZED");
                            c.put("analysisId", ac.getAnalysisId());
                            c.put("analysisType", ac.getAnalysisType() != null ? ac.getAnalysisType().name() : null);
                        } else {
                            c.put("analysisStatus", "NOT_ANALYZED");
                        }

                        commits.add(c);
                    }
                } catch (Exception e) {
                    log.warn("Failed to fetch commit history for branch {} (project={}): {}",
                            branchName, projectId, e.getMessage());
                }

                if (!branchNames.contains(branchName)) {
                    branchList.add(branchInfo);
                    branchNames.add(branchName);
                }
            }
        } catch (Exception e) {
            log.warn("Failed to fetch git graph from VCS for project {}: {}", projectId, e.getMessage());
        }

        // Also include ALL DB branches in the branch list (metadata only, no commit fetch)
        // so the frontend branch-selector still has the full list available
        for (Branch dbBranch : dbBranches) {
            if (!branchNames.contains(dbBranch.getBranchName())) {
                Map<String, Object> branchInfo = new LinkedHashMap<>();
                branchInfo.put("name", dbBranch.getBranchName());
                branchInfo.put("headCommit", dbBranch.getCommitHash());
                branchInfo.put("healthStatus", dbBranch.getHealthStatus() != null ? dbBranch.getHealthStatus().name() : null);
                branchInfo.put("totalIssues", dbBranch.getTotalIssues());
                branchInfo.put("highSeverity", dbBranch.getHighSeverityCount());
                branchInfo.put("mediumSeverity", dbBranch.getMediumSeverityCount());
                branchInfo.put("lowSeverity", dbBranch.getLowSeverityCount());
                branchList.add(branchInfo);
                branchNames.add(dbBranch.getBranchName());
            }
        }

        // 5. Second pass: enrich commits with CodeAnalysis data (result, issues, PR info)
        if (!commits.isEmpty()) {
            List<String> allHashes = commits.stream()
                    .map(c -> (String) c.get("hash"))
                    .collect(Collectors.toList());

            // Bulk-load CodeAnalysis records for all collected commit hashes
            List<CodeAnalysis> analyses = codeAnalysisRepository.findByProjectIdAndCommitHashIn(projectId, allHashes);
            Map<String, CodeAnalysis> analysisMap = new LinkedHashMap<>();
            for (CodeAnalysis ca : analyses) {
                // Keep the most recent analysis per commit hash
                analysisMap.merge(ca.getCommitHash(), ca,
                        (existing, newer) -> newer.getCreatedAt().isAfter(existing.getCreatedAt()) ? newer : existing);
            }

            for (Map<String, Object> c : commits) {
                String hash = (String) c.get("hash");
                CodeAnalysis ca = analysisMap.get(hash);
                if (ca != null) {
                    // Analysis result (PASSED / FAILED / SKIPPED)
                    c.put("analysisResult", ca.getAnalysisResult() != null ? ca.getAnalysisResult().name() : null);

                    // Issue counts
                    c.put("totalIssues", ca.getTotalIssues());
                    c.put("highSeverity", ca.getHighSeverityCount());
                    c.put("mediumSeverity", ca.getMediumSeverityCount());
                    c.put("lowSeverity", ca.getLowSeverityCount());

                    // PR info
                    c.put("prNumber", ca.getPrNumber());
                    if (ca.getPrNumber() != null) {
                        c.put("prId", prNumberToInternalId.get(ca.getPrNumber()));
                    }

                    // Branch context
                    c.put("sourceBranch", ca.getSourceBranchName());
                    c.put("targetBranch", ca.getBranchName());
                }
            }
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("commits", commits);
        result.put("branches", branchList);
        return ResponseEntity.ok(result);
    }
}
