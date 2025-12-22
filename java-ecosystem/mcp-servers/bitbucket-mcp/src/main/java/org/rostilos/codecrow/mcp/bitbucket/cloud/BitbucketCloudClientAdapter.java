package org.rostilos.codecrow.mcp.bitbucket.cloud;

import org.rostilos.codecrow.mcp.bitbucket.cloud.model.*;
import org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff.FileDiff;
import org.rostilos.codecrow.mcp.generic.FileDiffInfo;
import org.rostilos.codecrow.mcp.generic.VcsMcpClient;

import java.io.IOException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

public class BitbucketCloudClientAdapter implements VcsMcpClient {

    private final BitbucketCloudClient delegate;

    public BitbucketCloudClientAdapter(BitbucketCloudClient delegate) {
        this.delegate = delegate;
    }

    public BitbucketCloudClient getUnderlyingClient() {
        return delegate;
    }

    @Override
    public String getProviderType() {
        return "bitbucket";
    }

    @Override
    public String getPrNumber() throws IOException {
        return delegate.getPrNumber();
    }

    @Override
    public String getPullRequestTitle() throws IOException {
        return delegate.getPullRequestTitle();
    }

    @Override
    public String getPullRequestDescription() throws IOException {
        return delegate.getPullRequestDescription();
    }

    @Override
    public List<FileDiffInfo> getPullRequestChanges() throws IOException {
        List<FileDiff> diffs = delegate.getPullRequestChanges();
        return diffs.stream()
                .map(d -> new FileDiffInfo(
                        d.getFilePath(),
                        d.getDiffType().name(),
                        d.getRawContent(),
                        d.getChanges()
                ))
                .toList();
    }

    @Override
    public List<Map<String, Object>> listRepositories(String workspace, Integer limit) throws IOException {
        List<BitbucketRepository> repos = delegate.listRepositories(workspace, limit);
        return repos.stream().map(this::toMap).toList();
    }

    @Override
    public Map<String, Object> getRepository(String workspace, String repoSlug) throws IOException {
        return toMap(delegate.getRepository(workspace, repoSlug));
    }

    @Override
    public List<Map<String, Object>> getPullRequests(String workspace, String repoSlug, String state, Integer limit) throws IOException {
        List<BitbucketPullRequest> prs = delegate.getPullRequests(workspace, repoSlug, state, limit);
        return prs.stream().map(this::toMap).toList();
    }

    @Override
    public Map<String, Object> createPullRequest(String workspace, String repoSlug, String title, String description, String sourceBranch, String targetBranch, List<String> reviewers) throws IOException {
        return toMap(delegate.createPullRequest(workspace, repoSlug, title, description, sourceBranch, targetBranch, reviewers));
    }

    @Override
    public Map<String, Object> getPullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException {
        return toMap(delegate.getPullRequest(workspace, repoSlug, pullRequestId));
    }

    @Override
    public Map<String, Object> updatePullRequest(String workspace, String repoSlug, String pullRequestId, String title, String description) throws IOException {
        return toMap(delegate.updatePullRequest(workspace, repoSlug, pullRequestId, title, description));
    }

    @Override
    public Object getPullRequestActivity(String workspace, String repoSlug, String pullRequestId) throws IOException {
        return delegate.getPullRequestActivity(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Object approvePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException {
        return delegate.approvePullRequest(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Object unapprovePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException {
        return delegate.unapprovePullRequest(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Object declinePullRequest(String workspace, String repoSlug, String pullRequestId, String message) throws IOException {
        return delegate.declinePullRequest(workspace, repoSlug, pullRequestId, message);
    }

    @Override
    public Object mergePullRequest(String workspace, String repoSlug, String pullRequestId, String message, String strategy) throws IOException {
        return delegate.mergePullRequest(workspace, repoSlug, pullRequestId, message, strategy);
    }

    @Override
    public Object getPullRequestComments(String workspace, String repoSlug, String pullRequestId) throws IOException {
        return delegate.getPullRequestComments(workspace, repoSlug, pullRequestId);
    }

    @Override
    public String getPullRequestDiff(String workspace, String repoSlug, String pullRequestId) throws IOException {
        return delegate.getPullRequestDiff(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Object getPullRequestCommits(String workspace, String repoSlug, String pullRequestId) throws IOException {
        return delegate.getPullRequestCommits(workspace, repoSlug, pullRequestId);
    }

    @Override
    public Map<String, Object> getBranchingModel(String workspace, String repoSlug) throws IOException {
        return toMap(delegate.getRepositoryBranchingModel(workspace, repoSlug));
    }

    @Override
    public Map<String, Object> getBranchingModelSettings(String workspace, String repoSlug) throws IOException {
        return toMap(delegate.getRepositoryBranchingModelSettings(workspace, repoSlug));
    }

    @Override
    public Map<String, Object> updateBranchingModelSettings(String workspace, String repoSlug, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) throws IOException {
        return toMap(delegate.updateRepositoryBranchingModelSettings(workspace, repoSlug, development, production, branchTypes));
    }

    @Override
    public String getBranchFileContent(String workspace, String repoSlug, String branch, String filePath) throws IOException {
        return delegate.getBranchFileContent(workspace, repoSlug, branch, filePath);
    }

    @Override
    public String getRootDirectory(String workspace, String repoSlug, String branch) throws IOException {
        return delegate.getRootDirectory(workspace, repoSlug, branch);
    }

    @Override
    public String getDirectoryByPath(String workspace, String repoSlug, String branch, String dirPath) throws IOException {
        return delegate.getDirectoryByPath(workspace, repoSlug, branch, dirPath);
    }

    private Map<String, Object> toMap(BitbucketRepository repo) {
        if (repo == null) return null;
        Map<String, Object> map = new HashMap<>();
        map.put("uuid", repo.uuid);
        map.put("name", repo.name);
        map.put("full_name", repo.fullName);
        map.put("description", repo.description);
        map.put("is_private", repo.isPrivate);
        map.put("slug", repo.slug);
        return map;
    }

    private Map<String, Object> toMap(BitbucketPullRequest pr) {
        if (pr == null) return null;
        Map<String, Object> map = new HashMap<>();
        map.put("id", pr.id);
        map.put("title", pr.title);
        map.put("description", pr.summary != null ? pr.summary.raw : null);
        map.put("state", pr.state);
        map.put("source_branch", pr.source != null && pr.source.branch != null ? pr.source.branch.name : null);
        map.put("target_branch", pr.destination != null && pr.destination.branch != null ? pr.destination.branch.name : null);
        map.put("author", pr.author != null ? pr.author.displayName : null);
        map.put("created_on", pr.createdOn);
        map.put("updated_on", pr.updatedOn);
        return map;
    }

    private Map<String, Object> toMap(BitbucketBranchingModel model) {
        if (model == null) return null;
        Map<String, Object> map = new HashMap<>();
        map.put("type", model.type);
        map.put("branch_types", model.branchTypes);
        map.put("development", model.development);
        map.put("production", model.production);
        return map;
    }

    private Map<String, Object> toMap(BitbucketBranchingModelSettings settings) {
        if (settings == null) return null;
        Map<String, Object> map = new HashMap<>();
        map.put("type", settings.type);
        map.put("branch_types", settings.branchTypes);
        map.put("development", settings.development);
        map.put("production", settings.production);
        return map;
    }
}
