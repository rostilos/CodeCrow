package org.rostilos.codecrow.webserver.analysis.controller;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.gitgraph.CommitNode;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.persistence.repository.gitgraph.CommitNodeRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;

import java.util.*;

/**
 * Returns the REAL git commit graph for a project.
 *
 * Uses CommitNode data (synced from VCS by GitGraphSyncService) to return
 * actual commits with their parent-child DAG edges. The frontend renders
 * this as a proper git graph (like gitk / SourceTree / GitKraken).
 *
 * Each commit carries:
 *   - hash, author, message, timestamp
 *   - parent hashes (the DAG edges)
 *   - analysis status (NOT_ANALYZED / ANALYZED / FAILED)
 *   - linked analysis details if analyzed (result, issues, PR number, etc.)
 *
 * Branches come from the Branch table with their HEAD commit hash, health
 * status, and issue counts. The frontend uses branch heads + first-parent
 * walks to assign commits to branch lanes.
 */
@RestController
@RequestMapping("/api/v1/projects/{projectId}/git-graph")
public class GitGraphController {

    private final CommitNodeRepository commitNodeRepository;
    private final BranchRepository branchRepository;

    public GitGraphController(CommitNodeRepository commitNodeRepository,
                              BranchRepository branchRepository) {
        this.commitNodeRepository = commitNodeRepository;
        this.branchRepository = branchRepository;
    }

    @GetMapping
    @Transactional(readOnly = true)
    public ResponseEntity<Map<String, Object>> getGitGraph(@PathVariable Long projectId) {

        // 1. Fetch all commit nodes with their linked analysis (eager)
        List<CommitNode> nodes = commitNodeRepository.findByProjectIdWithAnalysis(projectId);

        // 2. Fetch all parent-child edges as hash pairs
        List<Object[]> edgeRows = commitNodeRepository.findEdgeHashesByProjectId(projectId);
        Map<String, List<String>> parentMap = new HashMap<>();
        for (Object[] row : edgeRows) {
            String childHash = (String) row[0];
            String parentHash = (String) row[1];
            parentMap.computeIfAbsent(childHash, k -> new ArrayList<>()).add(parentHash);
        }

        // 3. Build commit DTOs
        List<Map<String, Object>> commits = new ArrayList<>();
        for (CommitNode node : nodes) {
            Map<String, Object> c = new LinkedHashMap<>();
            c.put("hash", node.getCommitHash());
            c.put("message", node.getCommitMessage());
            c.put("author", node.getAuthorName());
            c.put("timestamp", node.getCommitTimestamp());
            c.put("parents", parentMap.getOrDefault(node.getCommitHash(), Collections.emptyList()));
            c.put("analysisStatus", node.getAnalysisStatus().name());

            // Include details from the linked CodeAnalysis if present
            CodeAnalysis analysis = node.getAnalysis();
            if (analysis != null) {
                c.put("analysisId", analysis.getId());
                c.put("analysisResult", analysis.getAnalysisResult() != null ? analysis.getAnalysisResult().name() : null);
                c.put("analysisType", analysis.getAnalysisType() != null ? analysis.getAnalysisType().name() : null);
                c.put("prNumber", analysis.getPrNumber());
                c.put("sourceBranch", analysis.getSourceBranchName());
                c.put("targetBranch", analysis.getBranchName());
                c.put("totalIssues", analysis.getTotalIssues());
                c.put("highSeverity", analysis.getHighSeverityCount());
                c.put("mediumSeverity", analysis.getMediumSeverityCount());
                c.put("lowSeverity", analysis.getLowSeverityCount());
            }

            commits.add(c);
        }

        // 4. Build branch DTOs (with HEAD commit hash for lane assignment)
        List<Branch> branches = branchRepository.findByProjectId(projectId);
        List<Map<String, Object>> branchList = new ArrayList<>();
        for (Branch b : branches) {
            Map<String, Object> info = new LinkedHashMap<>();
            info.put("name", b.getBranchName());
            info.put("headCommit", b.getCommitHash());
            info.put("healthStatus", b.getHealthStatus() != null ? b.getHealthStatus().name() : null);
            info.put("totalIssues", b.getTotalIssues());
            info.put("highSeverity", b.getHighSeverityCount());
            info.put("mediumSeverity", b.getMediumSeverityCount());
            info.put("lowSeverity", b.getLowSeverityCount());
            branchList.add(info);
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("commits", commits);
        result.put("branches", branchList);
        return ResponseEntity.ok(result);
    }
}
