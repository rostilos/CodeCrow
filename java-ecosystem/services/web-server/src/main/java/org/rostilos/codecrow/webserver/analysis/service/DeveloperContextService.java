package org.rostilos.codecrow.webserver.analysis.service;

import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

@Service
public class DeveloperContextService {
    private final CodeAnalysisRepository analyses;
    public DeveloperContextService(CodeAnalysisRepository analyses) { this.analyses = analyses; }

    @Transactional(readOnly = true)
    public Map<String, Object> get(Long projectId, String branch, String commitHash, Long prNumber) {
        branch = branch == null || branch.isBlank() ? null : branch;
        commitHash = commitHash == null || commitHash.isBlank() ? null : commitHash;
        if (prNumber == null && branch == null && commitHash == null) {
            return Map.of("available", false, "diagnostic", "Provide the current branch, commit, or PR number.");
        }
        var page = PageRequest.of(0, 1);
        var matches = analyses.findDeveloperContext(projectId, prNumber, commitHash, prNumber == null ? branch : null, page);
        if (matches.isEmpty() && commitHash != null && (prNumber != null || branch != null)) {
            matches = analyses.findDeveloperContext(projectId, prNumber, null, prNumber == null ? branch : null, page);
        }
        if (matches.isEmpty()) {
            return Map.of("available", false, "diagnostic", "No analysis exists for this PR, branch, or commit. Uncommitted changes have not been analyzed.");
        }
        var analysis = matches.get(0);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("available", true);
        result.put("analysisId", analysis.getId());
        result.put("prNumber", analysis.getPrNumber());
        result.put("commitHash", analysis.getCommitHash());
        result.put("sourceBranch", analysis.getSourceBranchName());
        result.put("targetBranch", analysis.getBranchName());
        result.put("analysisStatus", analysis.getStatus());
        result.put("summary", analysis.getComment());
        boolean matchesCommit = commitHash != null && Objects.equals(commitHash, analysis.getCommitHash());
        result.put("matchesLocalCommit", matchesCommit);
        result.put("diagnostic", matchesCommit
                ? "Findings match the supplied commit. Validate them against any uncommitted edits before fixing."
                : "These are the latest available findings for this branch or PR; they may differ from your checkout. Verify paths and lines before applying fixes.");
        result.put("issues", analysis.getIssues().stream().filter(issue -> !issue.isResolved()).map(IssueDTO::fromEntity).toList());
        return result;
    }
}
