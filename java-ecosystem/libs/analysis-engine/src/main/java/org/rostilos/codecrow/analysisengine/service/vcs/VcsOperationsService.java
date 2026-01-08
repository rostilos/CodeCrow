package org.rostilos.codecrow.analysisengine.service.vcs;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;

import java.io.IOException;

/**
 * Generic interface for VCS operations that vary by provider.
 * Implementations handle provider-specific API calls for operations like
 * fetching diffs and checking file existence.
 */
public interface VcsOperationsService {

    /**
     * @return the VCS provider this service handles
     */
    EVcsProvider getProvider();

    /**
     * Fetches the raw diff for a commit.
     *
     * @param client authorized HTTP client
     * @param workspace workspace or team/organization slug
     * @param repoSlug repository slug
     * @param commitHash commit hash
     * @return raw unified diff as returned by VCS API
     * @throws IOException on network / parsing errors
     */
    String getCommitDiff(OkHttpClient client, String workspace, String repoSlug, String commitHash) throws IOException;

    /**
     * Fetches the raw diff for a pull request.
     * This returns ALL files changed in the PR, not just the merge commit.
     *
     * @param client authorized HTTP client
     * @param workspace workspace or team/organization slug
     * @param repoSlug repository slug
     * @param prNumber pull request number
     * @return raw unified diff as returned by VCS API
     * @throws IOException on network / parsing errors
     */
    String getPullRequestDiff(OkHttpClient client, String workspace, String repoSlug, String prNumber) throws IOException;

    /**
     * Fetches the diff between two commits (delta diff for incremental analysis).
     * This is used to get only the changes made since the last analyzed commit.
     *
     * @param client authorized HTTP client
     * @param workspace workspace or team/organization slug
     * @param repoSlug repository slug
     * @param baseCommitHash the base commit (previously analyzed commit)
     * @param headCommitHash the head commit (current commit to analyze)
     * @return raw unified diff between the two commits
     * @throws IOException on network / parsing errors
     */
    String getCommitRangeDiff(OkHttpClient client, String workspace, String repoSlug, String baseCommitHash, String headCommitHash) throws IOException;

    /**
     * Checks if a file exists in the specified branch.
     *
     * @param client authorized HTTP client
     * @param workspace workspace or team/organization slug
     * @param repoSlug repository slug
     * @param branchName branch name (or commit hash)
     * @param filePath file path relative to repository root
     * @return true if file exists in the branch, false otherwise
     * @throws IOException on network errors
     */
    boolean checkFileExistsInBranch(OkHttpClient client, String workspace, String repoSlug, String branchName, String filePath) throws IOException;

    /**
     * Finds the pull request number that introduced a specific commit to the repository.
     * This is useful for branch reconciliation when we need to track which PR resolved an issue.
     *
     * @param client authorized HTTP client
     * @param workspace workspace or team/organization slug
     * @param repoSlug repository slug
     * @param commitHash the commit hash to look up
     * @return the PR/MR number that introduced this commit, or null if not found or commit wasn't from a PR
     * @throws IOException on network errors
     */
    Long findPullRequestForCommit(OkHttpClient client, String workspace, String repoSlug, String commitHash) throws IOException;
}
