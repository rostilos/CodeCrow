package org.rostilos.codecrow.mcp.bitbucket.cloud;

import org.rostilos.codecrow.mcp.bitbucket.cloud.model.*;
import org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff.FileDiff;

import java.io.IOException;
import java.nio.file.*;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Minimal BitbucketCloudClient implementation that reads repository data from a local directory.
 * This is intended for "local mode" where the MCP server must operate on files supplied by the pipeline agent.
 *
 * Note: This implementation provides basic filesystem-backed implementations for:
 *   - getBranchFileContent
 *   - getRootDirectory
 *   - getDirectoryByPath
 *
 * Many other methods are either not applicable in local mode or return simple placeholders.
 * Extend as needed for more complete behavior.
 */
//TODO: make generic, extend default client
public class LocalRepoClient implements BitbucketCloudClient {

    private final Path repoRoot;

    public LocalRepoClient(String repoRootPath) {
        this.repoRoot = Paths.get(repoRootPath).toAbsolutePath().normalize();
    }

    private Path resolveRelative(String branchOrIgnored, String filePath) {
        // branch parameter is ignored for local-mode; files are read directly from the provided repoRoot.
        Path candidate = repoRoot.resolve(filePath).normalize();
        if (!candidate.startsWith(repoRoot)) {
            // prevent path traversal
            return null;
        }
        return candidate;
    }

    @Override
    public String getBranchFileContent(String workspace, String repoSlug, String branch, String filePath) throws IOException {
        Path resolved = resolveRelative(branch, filePath);
        if (resolved == null || !Files.exists(resolved)) {
            throw new IOException("File not found: " + filePath);
        }
        return Files.readString(resolved);
    }

    @Override
    public String getRootDirectory(String workspace, String repoSlug, String branch) throws IOException {
        // Return a simple newline-separated listing of files/directories at repo root.
        StringBuilder sb = new StringBuilder();
        try (DirectoryStream<Path> stream = Files.newDirectoryStream(repoRoot)) {
            for (Path entry : stream) {
                sb.append(entry.getFileName().toString());
                if (Files.isDirectory(entry)) {
                    sb.append("/");
                }
                sb.append("\n");
            }
        } catch (IOException e) {
            throw new IOException("Failed to read repository root: " + e.getMessage(), e);
        }
        return sb.toString();
    }

    @Override
    public String getDirectoryByPath(String workspace, String repoSlug, String branch, String dirPath) throws IOException {
        Path dir = resolveRelative(branch, dirPath);
        if (dir == null || !Files.exists(dir) || !Files.isDirectory(dir)) {
            throw new IOException("Directory not found: " + dirPath);
        }
        StringBuilder sb = new StringBuilder();
        try (DirectoryStream<Path> stream = Files.newDirectoryStream(dir)) {
            for (Path entry : stream) {
                sb.append(entry.getFileName().toString());
                if (Files.isDirectory(entry)) {
                    sb.append("/");
                }
                sb.append("\n");
            }
        } catch (IOException e) {
            throw new IOException("Failed to read directory: " + e.getMessage(), e);
        }
        return sb.toString();
    }

    @Override
    public String getPrNumber() throws IOException {
        throw new IOException("getPrNumber is not available in local mode");
    }

    @Override
    public String getPullRequestTitle() throws IOException {
        throw new IOException("Pull request metadata is not available in local mode");
    }

    @Override
    public String getPullRequestDescription() throws IOException {
        throw new IOException("Pull request metadata is not available in local mode");
    }

    @Override
    public List<FileDiff> getPullRequestChanges() throws IOException {
        // Local mode doesn't have PR metadata. Return empty list.
        return Collections.emptyList();
    }

    @Override
    public List<BitbucketRepository> listRepositories(String workspace, Integer limit) throws IOException {
        throw new IOException("listRepositories is not supported in local mode");
    }

    @Override
    public BitbucketRepository getRepository(String workspace, String repoSlug) throws IOException {
        throw new IOException("getRepository is not supported in local mode");
    }

    @Override
    public List<BitbucketPullRequest> getPullRequests(String workspace, String repoSlug, String state, Integer limit) throws IOException {
        throw new IOException("getPullRequests is not supported in local mode");
    }

    @Override
    public BitbucketPullRequest createPullRequest(String workspace, String repoSlug, String title, String description, String sourceBranch, String targetBranch, List<String> reviewers) throws IOException {
        throw new IOException("createPullRequest is not supported in local mode");
    }

    @Override
    public BitbucketPullRequest getPullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException {
        throw new IOException("getPullRequest is not supported in local mode");
    }

    @Override
    public BitbucketPullRequest updatePullRequest(String workspace, String repoSlug, String pullRequestId, String title, String description) throws IOException {
        throw new IOException("updatePullRequest is not supported in local mode");
    }

    @Override
    public Object getPullRequestActivity(String workspace, String repoSlug, String pullRequestId) throws IOException {
        throw new IOException("getPullRequestActivity is not supported in local mode");
    }

    @Override
    public Object approvePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException {
        throw new IOException("approvePullRequest is not supported in local mode");
    }

    @Override
    public Object unapprovePullRequest(String workspace, String repoSlug, String pullRequestId) throws IOException {
        throw new IOException("unapprovePullRequest is not supported in local mode");
    }

    @Override
    public Object declinePullRequest(String workspace, String repoSlug, String pullRequestId, String message) throws IOException {
        throw new IOException("declinePullRequest is not supported in local mode");
    }

    @Override
    public Object mergePullRequest(String workspace, String repoSlug, String pullRequestId, String message, String strategy) throws IOException {
        throw new IOException("mergePullRequest is not supported in local mode");
    }

    @Override
    public Object getPullRequestComments(String workspace, String repoSlug, String pullRequestId) throws IOException {
        throw new IOException("getPullRequestComments is not supported in local mode");
    }

    @Override
    public String getPullRequestDiff(String workspace, String repoSlug, String pullRequestId) throws IOException {
        throw new IOException("getPullRequestDiff is not supported in local mode");
    }

    @Override
    public Object getPullRequestCommits(String workspace, String repoSlug, String pullRequestId) throws IOException {
        throw new IOException("getPullRequestCommits is not supported in local mode");
    }

    @Override
    public BitbucketBranchingModel getRepositoryBranchingModel(String workspace, String repoSlug) throws IOException {
        throw new IOException("getRepositoryBranchingModel is not supported in local mode");
    }

    @Override
    public BitbucketBranchingModelSettings getRepositoryBranchingModelSettings(String workspace, String repoSlug) throws IOException {
        throw new IOException("getRepositoryBranchingModelSettings is not supported in local mode");
    }

    @Override
    public BitbucketBranchingModelSettings updateRepositoryBranchingModelSettings(String workspace, String repoSlug, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) throws IOException {
        throw new IOException("updateRepositoryBranchingModelSettings is not supported in local mode");
    }

    @Override
    public BitbucketBranchingModel getEffectiveRepositoryBranchingModel(String workspace, String repoSlug) throws IOException {
        throw new IOException("getEffectiveRepositoryBranchingModel is not supported in local mode");
    }

    @Override
    public BitbucketProjectBranchingModel getProjectBranchingModel(String workspace, String projectKey) throws IOException {
        throw new IOException("getProjectBranchingModel is not supported in local mode");
    }

    @Override
    public BitbucketProjectBranchingModel getProjectBranchingModelSettings(String workspace, String projectKey) throws IOException {
        throw new IOException("getProjectBranchingModelSettings is not supported in local mode");
    }

    @Override
    public BitbucketProjectBranchingModel updateProjectBranchingModelSettings(String workspace, String projectKey, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) throws IOException {
        throw new IOException("updateProjectBranchingModelSettings is not supported in local mode");
    }
}
