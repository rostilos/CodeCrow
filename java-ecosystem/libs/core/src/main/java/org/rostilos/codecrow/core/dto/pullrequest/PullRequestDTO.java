package org.rostilos.codecrow.core.dto.pullrequest;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;

public record PullRequestDTO(
        Long id,
        Long prNumber,
        String commitHash,
        String targetBranchName,
        String sourceBranchName,
        AnalysisResult analysisResult,
        Integer highSeverityCount,
        Integer mediumSeverityCount,
        Integer lowSeverityCount,
        Integer infoSeverityCount,
        Integer totalIssues
) {
    public static PullRequestDTO fromPullRequest(PullRequest pullRequest) {
        return new PullRequestDTO(
                pullRequest.getId(),
                pullRequest.getPrNumber(),
                pullRequest.getCommitHash(),
                pullRequest.getTargetBranchName(),
                pullRequest.getSourceBranchName(),
                null,
                null,
                null,
                null,
                null,
                null
        );
    }

    public static PullRequestDTO fromPullRequestWithAnalysis(PullRequest pullRequest, CodeAnalysis analysis) {
        if (analysis == null) {
            return fromPullRequest(pullRequest);
        }
        return new PullRequestDTO(
                pullRequest.getId(),
                pullRequest.getPrNumber(),
                pullRequest.getCommitHash(),
                pullRequest.getTargetBranchName(),
                pullRequest.getSourceBranchName(),
                analysis.getAnalysisResult(),
                analysis.getHighSeverityCount(),
                analysis.getMediumSeverityCount(),
                analysis.getLowSeverityCount(),
                analysis.getInfoSeverityCount(),
                analysis.getTotalIssues()
        );
    }
}
