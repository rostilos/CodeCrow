package org.rostilos.codecrow.core.dto.pullrequest;

import org.rostilos.codecrow.core.model.pullrequest.PullRequest;

public record PullRequestDTO(
        Long id,
        Long prNumber,
        String commitHash,
        String targetBranchName,
        String sourceBranchName
) {
    public static PullRequestDTO fromPullRequest(PullRequest pullRequest) {
        return new PullRequestDTO(
                pullRequest.getId(),
                pullRequest.getPrNumber(),
                pullRequest.getCommitHash(),
                pullRequest.getTargetBranchName(),
                pullRequest.getSourceBranchName()
        );
    }
}
