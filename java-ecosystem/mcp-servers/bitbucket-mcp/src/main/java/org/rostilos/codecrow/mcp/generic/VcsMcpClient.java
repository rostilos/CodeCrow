package org.rostilos.codecrow.mcp.generic;

import java.io.IOException;
import java.util.List;
import java.util.Map;

public interface VcsMcpClient {
    
    String getProviderType();
    
    String getPrNumber() throws IOException;
    String getPullRequestTitle() throws IOException;
    String getPullRequestDescription() throws IOException;
    List<FileDiffInfo> getPullRequestChanges() throws IOException;

    List<Map<String, Object>> listRepositories(String workspace, Integer limit) throws IOException;
    Map<String, Object> getRepository(String workspace, String repoSlug) throws IOException;
    List<Map<String, Object>> getPullRequests(String workspace, String repoSlug, String state, Integer limit) throws IOException;
    Map<String, Object> createPullRequest(String workspace, String repoSlug, String title, String description, String sourceBranch, String targetBranch, List<String> reviewers) throws IOException;
    Map<String, Object> getPullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException;
    Map<String, Object> updatePullRequest(String workspace, String repoSlug, String pullRequestId, String title, String description) throws IOException;
    Object getPullRequestActivity(String workspace, String repoSlug, String pullRequestId) throws IOException;
    Object approvePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException;
    Object unapprovePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException;
    Object declinePullRequest(String workspace, String repoSlug, String pullRequestId, String message) throws IOException;
    Object mergePullRequest(String workspace, String repoSlug, String pullRequestId, String message, String strategy) throws IOException;
    Object getPullRequestComments(String workspace, String repoSlug, String pullRequestId) throws IOException;
    String getPullRequestDiff(String workspace, String repoSlug, String pullRequestId) throws IOException;
    Object getPullRequestCommits(String workspace, String repoSlug, String pullRequestId) throws IOException;
    
    Map<String, Object> getBranchingModel(String workspace, String repoSlug) throws IOException;
    Map<String, Object> getBranchingModelSettings(String workspace, String repoSlug) throws IOException;
    Map<String, Object> updateBranchingModelSettings(String workspace, String repoSlug, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) throws IOException;
    
    String getBranchFileContent(String workspace, String repoSlug, String branch, String filePath) throws IOException;
    String getRootDirectory(String workspace, String repoSlug, String branch) throws IOException;
    String getDirectoryByPath(String workspace, String repoSlug, String branch, String dirPath) throws IOException;
}
