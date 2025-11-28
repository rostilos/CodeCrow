package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Service for managing pull request lifecycle.
 */
@Service
public class PullRequestService {
    private static final Logger log = LoggerFactory.getLogger(PullRequestService.class);

    private final PullRequestRepository pullRequestRepository;

    public PullRequestService(PullRequestRepository pullRequestRepository) {
        this.pullRequestRepository = pullRequestRepository;
    }

    /**
     * Creates a new pull request or updates an existing one with new commit hash.
     *
     * @param projectId The project identifier
     * @param prNumber The pull request number from the VCS platform
     * @param commitHash The latest commit hash
     * @param sourceBranch The source branch name
     * @param targetBranch The target branch name
     * @param project The project entity
     * @return The created or updated pull request
     */
    @Transactional
    public PullRequest createOrUpdatePullRequest(
            Long projectId,
            Long prNumber,
            String commitHash,
            String sourceBranch,
            String targetBranch,
            Project project
    ) {
        return pullRequestRepository.findByPrNumberAndProject_id(prNumber, projectId)
                .map(existing -> updateExistingPullRequest(existing, commitHash))
                .orElseGet(() -> createNewPullRequest(
                        project, prNumber, commitHash, sourceBranch, targetBranch
                ));
    }

    private PullRequest updateExistingPullRequest(PullRequest pullRequest, String commitHash) {
        log.debug("Updating existing pull request {} with commit {}",
                pullRequest.getId(), commitHash);
        pullRequest.setCommitHash(commitHash);
        return pullRequestRepository.save(pullRequest);
    }

    private PullRequest createNewPullRequest(
            Project project,
            Long prNumber,
            String commitHash,
            String sourceBranch,
            String targetBranch
    ) {
        log.debug("Creating new pull request {} for project {}", prNumber, project.getId());

        PullRequest newPullRequest = new PullRequest();
        newPullRequest.setProject(project);
        newPullRequest.setPrNumber(prNumber);
        newPullRequest.setCommitHash(commitHash);
        newPullRequest.setSourceBranchName(sourceBranch);
        newPullRequest.setTargetBranchName(targetBranch);

        return pullRequestRepository.save(newPullRequest);
    }
}