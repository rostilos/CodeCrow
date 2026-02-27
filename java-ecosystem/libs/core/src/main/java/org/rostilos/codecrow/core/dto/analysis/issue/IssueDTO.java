package org.rostilos.codecrow.core.dto.analysis.issue;

import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.DetectionSource;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;

import java.time.OffsetDateTime;

public record IssueDTO (
    String id,
    String type, // security|quality|performance|style
    String severity, // critical|high|medium|low|info
    String title,
    String description,
    String suggestedFixDescription,
    String suggestedFixDiff,
    String file,
    Integer line,
    Integer column,
    String rule,
    String branch,
    String pullRequestId,
    String status, // open|resolved|ignored
    OffsetDateTime createdAt,
    String issueCategory,
    // Detection info - where was this issue first found
    Long analysisId,
    Long prNumber,
    String commitHash,
    OffsetDateTime detectedAt,
    // Resolution info - populated when issue is resolved
    String resolvedDescription,
    Long resolvedByPr,
    String resolvedCommitHash,
    Long resolvedAnalysisId,
    OffsetDateTime resolvedAt,
    String resolvedBy,
    // VCS author info - who created the PR that introduced this issue
    String vcsAuthorId,
    String vcsAuthorUsername,
    // Detection source - PR_ANALYSIS or DIRECT_PUSH_ANALYSIS
    String detectionSource
) {

    /**
     * Create an IssueDTO from an independent {@link BranchIssue}.
     * Reads all data from BranchIssue's own fields — never dereferences to CodeAnalysisIssue.
     */
    public static IssueDTO fromBranchIssue(BranchIssue bi) {
        String categoryStr = bi.getIssueCategory() != null
            ? bi.getIssueCategory().name()
            : IssueCategory.CODE_QUALITY.name();

        String title = bi.getTitle();
        if (title == null || title.isBlank()) {
            String reason = bi.getReason();
            if (reason != null && reason.length() > 120) {
                title = reason.substring(0, 117) + "...";
            } else {
                title = reason;
            }
        }

        // Use currentLineNumber (branch-reconciled position) if available,
        // otherwise fall back to the original detection lineNumber.
        Integer effectiveLine = bi.getCurrentLineNumber() != null
                ? bi.getCurrentLineNumber() : bi.getLineNumber();

        return new IssueDTO(
                String.valueOf(bi.getId()),
                categoryStr,
                bi.getSeverity() != null ? bi.getSeverity().name().toLowerCase() : null,
                title,
                bi.getReason(),
                bi.getSuggestedFixDescription(),
                bi.getSuggestedFixDiff(),
                bi.getFilePath(),
                effectiveLine,
                null,
                null,
                bi.getBranch() != null ? bi.getBranch().getBranchName() : bi.getOriginBranchName(),
                bi.getOriginPrNumber() != null ? String.valueOf(bi.getOriginPrNumber()) : null,
                bi.isResolved() ? "resolved" : "open",
                bi.getCreatedAt(),
                categoryStr,
                // Detection info — from provenance fields
                bi.getOriginAnalysisId(),
                bi.getOriginPrNumber(),
                bi.getOriginCommitHash(),
                bi.getCreatedAt(),
                // Resolution info
                bi.getResolvedDescription(),
                bi.getResolvedInPrNumber(),
                bi.getResolvedInCommitHash(),
                null, // resolvedAnalysisId — not tracked on BranchIssue
                bi.getResolvedAt(),
                bi.getResolvedBy(),
                bi.getVcsAuthorId(),
                bi.getVcsAuthorUsername(),
                bi.getDetectionSource() != null ? bi.getDetectionSource().name() : null
        );
    }

    public static IssueDTO fromEntity(CodeAnalysisIssue issue) {
        String categoryStr = issue.getIssueCategory() != null 
            ? issue.getIssueCategory().name() 
            : IssueCategory.CODE_QUALITY.name();
        
        var analysis = issue.getAnalysis();
        String title = issue.getTitle();
        if (title == null || title.isBlank()) {
            // Fallback: derive title from reason
            String reason = issue.getReason();
            if (reason != null && reason.length() > 120) {
                title = reason.substring(0, 117) + "...";
            } else {
                title = reason;
            }
        }
        return new IssueDTO(
                String.valueOf(issue.getId()),
                categoryStr,
                issue.getSeverity() != null ? issue.getSeverity().name().toLowerCase() : null,
                title,
                issue.getReason(),
                issue.getSuggestedFixDescription(),
                issue.getSuggestedFixDiff(),
                issue.getFilePath(),
                issue.getLineNumber(),
                null,
                null,
                analysis == null ? null : analysis.getBranchName(),
                analysis == null || analysis.getPrNumber() == null ? null : String.valueOf(analysis.getPrNumber()),
                issue.isResolved() ? "resolved" : "open",
                issue.getCreatedAt(),
                categoryStr,
                // Detection info
                analysis != null ? analysis.getId() : null,
                analysis != null ? analysis.getPrNumber() : null,
                analysis != null ? analysis.getCommitHash() : null,
                issue.getCreatedAt(),
                // Resolution info
                issue.getResolvedDescription(),
                issue.getResolvedByPr(),
                issue.getResolvedCommitHash(),
                issue.getResolvedAnalysisId(),
                issue.getResolvedAt(),
                issue.getResolvedBy(),
                issue.getVcsAuthorId(),
                issue.getVcsAuthorUsername(),
                issue.getDetectionSource() != null ? issue.getDetectionSource().name() : null
        );
    }
}
