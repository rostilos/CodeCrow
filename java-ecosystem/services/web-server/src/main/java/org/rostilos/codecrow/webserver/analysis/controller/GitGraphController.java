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
import org.rostilos.codecrow.security.annotations.IsProjectWorkspaceMember;
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
@IsProjectWorkspaceMember
@RequestMapping("/api/v1/projects/{projectId}/git-graph")
public class GitGraphController {

    private static final Logger log = LoggerFactory.getLogger(GitGraphController.class);
    private static final int DEFAULT_COMMIT_LIMIT = 100;
    private static final int MAX_GRAPH_COMMITS = 250;
    private static final int MERGE_PARENT_HISTORY_LIMIT = 50;
    private static final int MAX_MERGE_PARENT_FETCHES = 8;

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
                        appendCommit(commits, seenHashes, vc, analyzedMap);
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

            // A branch-history endpoint can omit the merged side when its tip is
            // older than the page boundary. Resolve those second-parent histories
            // explicitly so the response contains the actual DAG instead of a
            // first-parent-looking straight line.
            Deque<String> missingMergeParents = collectMissingMergeParents(commits, seenHashes);
            Set<String> attemptedRefs = new HashSet<>();
            int fetches = 0;
            while (!missingMergeParents.isEmpty()
                    && commits.size() < MAX_GRAPH_COMMITS
                    && fetches < MAX_MERGE_PARENT_FETCHES) {
                String parentRef = missingMergeParents.removeFirst();
                if (!attemptedRefs.add(parentRef) || seenHashes.contains(parentRef)) {
                    continue;
                }
                fetches++;
                try {
                    int remaining = MAX_GRAPH_COMMITS - commits.size();
                    List<VcsCommit> parentHistory = vcsClient.getCommitHistory(
                            ws,
                            slug,
                            parentRef,
                            Math.min(MERGE_PARENT_HISTORY_LIMIT, remaining));
                    for (VcsCommit vc : parentHistory) {
                        appendCommit(commits, seenHashes, vc, analyzedMap);
                    }
                    missingMergeParents.addAll(
                            collectMissingMergeParents(commits, seenHashes));
                } catch (Exception e) {
                    log.debug("Could not expand merge parent {} for project {}: {}",
                            parentRef, projectId, e.getMessage());
                }
            }
        } catch (Exception e) {
            log.warn("Failed to fetch git graph from VCS for project {}: {}", projectId, e.getMessage());
        }

        commits = topologicallyOrderCommits(commits);

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

    private static void appendCommit(
            List<Map<String, Object>> commits,
            Set<String> seenHashes,
            VcsCommit vc,
            Map<String, AnalyzedCommit> analyzedMap) {
        if (vc == null || vc.hash() == null || !seenHashes.add(vc.hash())) {
            return;
        }

        Map<String, Object> commit = new LinkedHashMap<>();
        commit.put("hash", vc.hash());
        commit.put("message", vc.message());
        commit.put("author", vc.authorName());
        commit.put("timestamp", vc.timestamp());
        commit.put("parents", vc.parentHashes() != null
                ? vc.parentHashes()
                : Collections.emptyList());

        AnalyzedCommit analyzed = analyzedMap.get(vc.hash());
        if (analyzed != null) {
            commit.put("analysisStatus", "ANALYZED");
            commit.put("analysisId", analyzed.getAnalysisId());
            commit.put("analysisType", analyzed.getAnalysisType() != null
                    ? analyzed.getAnalysisType().name()
                    : null);
        } else {
            commit.put("analysisStatus", "NOT_ANALYZED");
        }
        commits.add(commit);
    }

    private static Deque<String> collectMissingMergeParents(
            List<Map<String, Object>> commits,
            Set<String> seenHashes) {
        Deque<String> missing = new ArrayDeque<>();
        Set<String> queued = new HashSet<>();
        for (Map<String, Object> commit : commits) {
            List<String> parents = parentHashes(commit);
            for (int index = 1; index < parents.size(); index++) {
                String parent = parents.get(index);
                if (parent != null && !seenHashes.contains(parent) && queued.add(parent)) {
                    missing.addLast(parent);
                }
            }
        }
        return missing;
    }

    /**
     * Produce the child-before-parent ordering expected by the graph rail
     * renderer. Provider APIs normally return this order per branch, but simply
     * concatenating two branch histories breaks it around merges.
     */
    static List<Map<String, Object>> topologicallyOrderCommits(
            List<Map<String, Object>> commits) {
        if (commits.size() < 2) {
            return commits;
        }

        Map<String, Map<String, Object>> byHash = new LinkedHashMap<>();
        for (Map<String, Object> commit : commits) {
            Object hash = commit.get("hash");
            if (hash != null) byHash.putIfAbsent(hash.toString(), commit);
        }

        Map<String, Integer> incomingChildren = new HashMap<>();
        byHash.keySet().forEach(hash -> incomingChildren.put(hash, 0));
        for (Map<String, Object> commit : byHash.values()) {
            for (String parent : parentHashes(commit)) {
                if (byHash.containsKey(parent)) {
                    incomingChildren.merge(parent, 1, Integer::sum);
                }
            }
        }

        Comparator<String> newestFirst = Comparator
                .comparing((String hash) -> timestampKey(byHash.get(hash)),
                        Comparator.reverseOrder())
                .thenComparing(Comparator.naturalOrder());
        PriorityQueue<String> ready = new PriorityQueue<>(newestFirst);
        incomingChildren.forEach((hash, degree) -> {
            if (degree == 0) ready.add(hash);
        });

        List<Map<String, Object>> ordered = new ArrayList<>(byHash.size());
        Set<String> emitted = new HashSet<>();
        while (!ready.isEmpty()) {
            String hash = ready.remove();
            if (!emitted.add(hash)) continue;
            Map<String, Object> commit = byHash.get(hash);
            ordered.add(commit);
            for (String parent : parentHashes(commit)) {
                if (!byHash.containsKey(parent)) continue;
                int remaining = incomingChildren.merge(parent, -1, Integer::sum);
                if (remaining == 0) ready.add(parent);
            }
        }

        // Defensive fallback for malformed/cyclic provider data.
        byHash.forEach((hash, commit) -> {
            if (emitted.add(hash)) ordered.add(commit);
        });
        return ordered;
    }

    private static String timestampKey(Map<String, Object> commit) {
        Object timestamp = commit != null ? commit.get("timestamp") : null;
        return timestamp != null ? timestamp.toString() : "";
    }

    @SuppressWarnings("unchecked")
    private static List<String> parentHashes(Map<String, Object> commit) {
        Object parents = commit.get("parents");
        return parents instanceof List<?> list
                ? (List<String>) list
                : Collections.emptyList();
    }
}
