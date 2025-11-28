package org.rostilos.codecrow.mcp.bitbucket.cloud;

import org.rostilos.codecrow.mcp.bitbucket.cloud.model.*;
import org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff.FileDiff;

import java.io.IOException;
import java.util.List;
import java.util.Map;

public interface BitbucketCloudClient {
    String getPrNumber() throws IOException;
    String getPullRequestTitle() throws IOException;
    String getPullRequestDescription() throws IOException;
    List<FileDiff> getPullRequestChanges() throws IOException;

    List<BitbucketRepository> listRepositories(String workspace, Integer limit) throws IOException;
    BitbucketRepository getRepository(String workspace, String repoSlug) throws IOException;
    List<BitbucketPullRequest> getPullRequests(String workspace, String repoSlug, String state, Integer limit) throws IOException;
    BitbucketPullRequest createPullRequest(String workspace, String repoSlug, String title, String description, String sourceBranch, String targetBranch, List<String> reviewers) throws IOException;
    BitbucketPullRequest getPullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException;
    BitbucketPullRequest updatePullRequest(String workspace, String repoSlug, String pullRequestId, String title, String description) throws IOException;
    Object getPullRequestActivity(String workspace, String repoSlug, String pullRequestId) throws IOException; // Return type needs to be more specific
    Object approvePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException; // Return type needs to be more specific
    Object unapprovePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException; // Return type needs to be more specific
    Object declinePullRequest(String workspace, String repoSlug, String pullRequestId, String message) throws IOException; // Return type needs to be more specific
    Object mergePullRequest(String workspace, String repoSlug, String pullRequestId, String message, String strategy) throws IOException; // Return type needs to be more specific
    Object getPullRequestComments(String workspace, String repoSlug, String pullRequestId) throws IOException; // Return type needs to be more specific
    String getPullRequestDiff(String workspace, String repoSlug, String pullRequestId) throws IOException;
    Object getPullRequestCommits(String workspace, String repoSlug, String pullRequestId) throws IOException; // Return type needs to be more specific
    BitbucketBranchingModel getRepositoryBranchingModel(String workspace, String repoSlug) throws IOException;
    BitbucketBranchingModelSettings getRepositoryBranchingModelSettings(String workspace, String repoSlug) throws IOException;
    BitbucketBranchingModelSettings updateRepositoryBranchingModelSettings(String workspace, String repoSlug, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) throws IOException;
    BitbucketBranchingModel getEffectiveRepositoryBranchingModel(String workspace, String repoSlug) throws IOException;
    BitbucketProjectBranchingModel getProjectBranchingModel(String workspace, String projectKey) throws IOException;
    BitbucketProjectBranchingModel getProjectBranchingModelSettings(String workspace, String projectKey) throws IOException;
    BitbucketProjectBranchingModel updateProjectBranchingModelSettings(String workspace, String projectKey, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) throws IOException;
    String getBranchFileContent(String workspace, String repoSlug, String branch, String filePath) throws  IOException;
    String getRootDirectory(String workspace, String repoSlug, String branch) throws IOException;
    String getDirectoryByPath(String workspace, String repoSlug, String branch, String dirPath) throws IOException;
}