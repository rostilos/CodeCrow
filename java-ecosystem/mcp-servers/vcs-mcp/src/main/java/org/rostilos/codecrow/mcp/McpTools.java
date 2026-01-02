package org.rostilos.codecrow.mcp;

import java.io.IOException;
import java.util.List;
import java.util.Map;

import org.rostilos.codecrow.mcp.bitbucket.cloud.BitbucketCloudClientAdapter;
import org.rostilos.codecrow.mcp.bitbucket.cloud.model.BitbucketBranchingModel;
import org.rostilos.codecrow.mcp.bitbucket.cloud.model.BitbucketBranchingModelSettings;
import org.rostilos.codecrow.mcp.bitbucket.cloud.model.BitbucketProjectBranchingModel;
import org.rostilos.codecrow.mcp.filter.LargeContentFilter;
import org.rostilos.codecrow.mcp.generic.VcsMcpClient;
import org.rostilos.codecrow.mcp.generic.VcsMcpClientFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class McpTools {
    private final VcsMcpClientFactory vcsMcpClientFactory;
    private VcsMcpClient vcsClient = null;
    private final LargeContentFilter largeContentFilter;
    private static final Logger LOGGER = LoggerFactory.getLogger(McpTools.class);

    public McpTools(VcsMcpClientFactory vcsMcpClientFactory) {
        this.vcsMcpClientFactory = vcsMcpClientFactory;
        this.largeContentFilter = new LargeContentFilter();
    }

    public Object execute(String toolName, Map<String, Object> arguments) throws IOException {
        switch (toolName) {
            case "listRepositories":
                return listRepositories(
                        (String) arguments.get("workspace"),
                        (Integer) arguments.get("limit")
                );
            case "getRepository":
                return getRepository(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug")
                );
            case "getPullRequests":
                return getPullRequests(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("state"),
                        (Integer) arguments.get("limit")
                );
            case "createPullRequest":
                return createPullRequest(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("title"),
                        (String) arguments.get("description"),
                        (String) arguments.get("sourceBranch"),
                        (String) arguments.get("targetBranch"),
                        (List<String>) arguments.get("reviewers")
                );
            case "getPullRequest":
                return getPullRequest(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId")
                );
            case "updatePullRequest":
                return updatePullRequest(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId"),
                        (String) arguments.get("title"),
                        (String) arguments.get("description")
                );
            case "getPullRequestActivity":
                return getPullRequestActivity(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId")
                );
            case "approvePullRequest":
                return approvePullRequest(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId")
                );
            case "unapprovePullRequest":
                return unapprovePullRequest(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId")
                );
            case "declinePullRequest":
                return declinePullRequest(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId"),
                        (String) arguments.get("message")
                );
            case "mergePullRequest":
                return mergePullRequest(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId"),
                        (String) arguments.get("message"),
                        (String) arguments.get("strategy")
                );
            case "getPullRequestComments":
                return getPullRequestComments(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId")
                );
            case "getPullRequestDiff":
                return getPullRequestDiff(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId")
                );
            case "getPullRequestCommits":
                return getPullRequestCommits(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("pullRequestId")
                );
            case "getRepositoryBranchingModel":
                return getRepositoryBranchingModel(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug")
                );
            case "getRepositoryBranchingModelSettings":
                return getRepositoryBranchingModelSettings(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug")
                );
            case "updateRepositoryBranchingModelSettings":
                return updateRepositoryBranchingModelSettings(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (Map<String, Object>) arguments.get("development"),
                        (Map<String, Object>) arguments.get("production"),
                        (List<Map<String, Object>>) arguments.get("branchTypes")
                );
            case "getEffectiveRepositoryBranchingModel":
                return getEffectiveRepositoryBranchingModel(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug")
                );
            case "getProjectBranchingModel":
                return getProjectBranchingModel(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("projectKey")
                );
            case "getProjectBranchingModelSettings":
                return getProjectBranchingModelSettings(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("projectKey")
                );
            case "updateProjectBranchingModelSettings":
                return updateProjectBranchingModelSettings(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("projectKey"),
                        (Map<String, Object>) arguments.get("development"),
                        (Map<String, Object>) arguments.get("production"),
                        (List<Map<String, Object>>) arguments.get("branchTypes")
                );

            case "getBranchFileContent":
                return getBranchFileContent(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("repoSlug"),
                        (String) arguments.get("branch"),
                        (String) arguments.get("filePath")
                );
            case "getRootDirectory":
                return getRootDirectory(
                        (String) arguments.get("workspace"),
                        (String) arguments.get("projectKey"),
                        (String) arguments.get("branch")
                );
            default:
                throw new IllegalArgumentException("Unknown tool: " + toolName);
        }
    }

    private VcsMcpClient getVcsClient() throws IOException {
        if (vcsClient == null) {
            vcsClient = vcsMcpClientFactory.createClient();
        }
        return vcsClient;
    }

    private VcsMcpClient getVcsClient(boolean callerToolLocalModeSupported) throws IOException {
        return getVcsClient();
    }

    public Map<String, Object> listRepositories(String workspace, Integer limit) {
        try {
            List<Map<String, Object>> repositories = getVcsClient().listRepositories(workspace, limit);
            return Map.of("repositories", repositories);
        } catch (IOException e) {
            return Map.of("error", "Failed to list repositories: " + e.getMessage());
        }
    }

    public Map<String, Object> getRepository(String workspace, String repoSlug) {
        try {
            Map<String, Object> repository = getVcsClient().getRepository(workspace, repoSlug);
            return Map.of("repository", repository);
        } catch (IOException e) {
            return Map.of("error", "Failed to get repository: " + e.getMessage());
        }
    }

    public Map<String, Object> getPullRequests(String workspace, String repoSlug, String state, Integer limit) {
        try {
            List<Map<String, Object>> pullRequests = getVcsClient().getPullRequests(workspace, repoSlug, state, limit);
            return Map.of("pullRequests", pullRequests);
        } catch (IOException e) {
            return Map.of("error", "Failed to get pull requests: " + e.getMessage());
        }
    }

    public Map<String, Object> createPullRequest(String workspace, String repoSlug, String title, String description, String sourceBranch, String targetBranch, List<String> reviewers) {
        try {
            Map<String, Object> pullRequest = getVcsClient().createPullRequest(workspace, repoSlug, title, description, sourceBranch, targetBranch, reviewers);
            return Map.of("pullRequest", pullRequest);
        } catch (IOException e) {
            return Map.of("error", "Failed to create pull request: " + e.getMessage());
        }
    }

    public Map<String, Object> getPullRequest(String workspace, String repoSlug, String pullRequestId) {
        try {
            Map<String, Object> pullRequest = getVcsClient().getPullRequest(workspace, repoSlug, pullRequestId);
            return Map.of("pullRequest", pullRequest);
        } catch (IOException e) {
            return Map.of("error", "Failed to get pull request: " + e.getMessage());
        }
    }

    public Map<String, Object> updatePullRequest(String workspace, String repoSlug, String pullRequestId, String title, String description) {
        try {
            Map<String, Object> pullRequest = getVcsClient().updatePullRequest(workspace, repoSlug, pullRequestId, title, description);
            return Map.of("pullRequest", pullRequest);
        } catch (IOException e) {
            return Map.of("error", "Failed to update pull request: " + e.getMessage());
        }
    }

    public Map<String, Object> getPullRequestActivity(String workspace, String repoSlug, String pullRequestId) {
        try {
            Object activity = getVcsClient().getPullRequestActivity(workspace, repoSlug, pullRequestId);
            return Map.of("activity", activity);
        } catch (IOException e) {
            return Map.of("error", "Failed to get pull request activity: " + e.getMessage());
        }
    }

    public Map<String, Object> approvePullRequest(String workspace, String repoSlug, String pullRequestId) {
        try {
            Object result = getVcsClient().approvePullRequest(workspace, repoSlug, pullRequestId);
            return Map.of("result", result);
        } catch (IOException e) {
            return Map.of("error", "Failed to approve pull request: " + e.getMessage());
        }
    }

    public Map<String, Object> unapprovePullRequest(String workspace, String repoSlug, String pullRequestId) {
        try {
            Object result = getVcsClient().unapprovePullRequest(workspace, repoSlug, pullRequestId);
            return Map.of("result", result);
        } catch (IOException e) {
            return Map.of("error", "Failed to unapprove pull request: " + e.getMessage());
        }
    }

    public Map<String, Object> declinePullRequest(String workspace, String repoSlug, String pullRequestId, String message) {
        try {
            Object result = getVcsClient().declinePullRequest(workspace, repoSlug, pullRequestId, message);
            return Map.of("result", result);
        } catch (IOException e) {
            return Map.of("error", "Failed to decline pull request: " + e.getMessage());
        }
    }

    public Map<String, Object> mergePullRequest(String workspace, String repoSlug, String pullRequestId, String message, String strategy) {
        try {
            Object result = getVcsClient().mergePullRequest(workspace, repoSlug, pullRequestId, message, strategy);
            return Map.of("result", result);
        } catch (IOException e) {
            return Map.of("error", "Failed to merge pull request: " + e.getMessage());
        }
    }

    public Map<String, Object> getPullRequestComments(String workspace, String repoSlug, String pullRequestId) {
        try {
            Object comments = getVcsClient().getPullRequestComments(workspace, repoSlug, pullRequestId);
            return Map.of("comments", comments);
        } catch (IOException e) {
            return Map.of("error", "Failed to get pull request comments: " + e.getMessage());
        }
    }

    public Map<String, Object> getPullRequestDiff(String workspace, String repoSlug, String pullRequestId) {
        try {
            String diff = getVcsClient().getPullRequestDiff(workspace, repoSlug, pullRequestId);
            // Filter large files from diff to reduce token usage
            String filteredDiff = largeContentFilter.filterDiff(diff);
            return Map.of("diff", filteredDiff);
        } catch (IOException e) {
            return Map.of("error", "Failed to get pull request diff: " + e.getMessage());
        }
    }

    public Map<String, Object> getPullRequestCommits(String workspace, String repoSlug, String pullRequestId) {
        try {
            Object commits = getVcsClient().getPullRequestCommits(workspace, repoSlug, pullRequestId);
            return Map.of("commits", commits);
        } catch (IOException e) {
            return Map.of("error", "Failed to get pull request commits: " + e.getMessage());
        }
    }

    public Map<String, Object> getBranchFileContent(String workspace, String repoSlug, String branch, String filePath) {
        try {
            String fileContent = getVcsClient(true).getBranchFileContent(workspace, repoSlug, branch, filePath);
            // Filter large file content to reduce token usage
            String filteredContent = largeContentFilter.filterFileContent(fileContent, filePath);
            return Map.of("fileContent", filteredContent);
        } catch (IOException e) {
            return Map.of("error", "Failed to get branch file content: " + e.getMessage());
        }
    }

    public Map<String,Object> getRootDirectory(String workspace, String projectKey, String branch) {
        try {
            String rootDirectory = getVcsClient().getRootDirectory(workspace, projectKey, branch);
            return Map.of("rootDirectory", rootDirectory);
        } catch (IOException e) {
            return Map.of("error", "Failed to get branch root repository tree: " + e.getMessage());
        }
    }

    public Map<String,Object> getDirectoryByPath(String workspace, String projectKey, String branch, String dirPath) {
        try {
            String directoryContent = getVcsClient().getDirectoryByPath(workspace, projectKey, branch, dirPath);
            return Map.of("directoryContent", directoryContent);
        } catch (IOException e) {
            return Map.of("error", "Failed to get branch directory content: " + e.getMessage());
        }
    }

    public Map<String, Object> getRepositoryBranchingModel(String workspace, String repoSlug) {
        try {
            VcsMcpClient client = getVcsClient();
            if (!"bitbucket".equals(client.getProviderType())) {
                return Map.of("error", "Branching model is only available for Bitbucket repositories");
            }
            BitbucketCloudClientAdapter adapter = (BitbucketCloudClientAdapter) client;
            BitbucketBranchingModel model = adapter.getUnderlyingClient().getRepositoryBranchingModel(workspace, repoSlug);
            return Map.of("branchingModel", model);
        } catch (IOException e) {
            return Map.of("error", "Failed to get repository branching model: " + e.getMessage());
        }
    }

    public Map<String, Object> getRepositoryBranchingModelSettings(String workspace, String repoSlug) {
        try {
            VcsMcpClient client = getVcsClient();
            if (!"bitbucket".equals(client.getProviderType())) {
                return Map.of("error", "Branching model settings are only available for Bitbucket repositories");
            }
            BitbucketCloudClientAdapter adapter = (BitbucketCloudClientAdapter) client;
            BitbucketBranchingModelSettings settings = adapter.getUnderlyingClient().getRepositoryBranchingModelSettings(workspace, repoSlug);
            return Map.of("branchingModelSettings", settings);
        } catch (IOException e) {
            return Map.of("error", "Failed to get repository branching model settings: " + e.getMessage());
        }
    }

    public Map<String, Object> updateRepositoryBranchingModelSettings(String workspace, String repoSlug, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) {
        try {
            VcsMcpClient client = getVcsClient();
            if (!"bitbucket".equals(client.getProviderType())) {
                return Map.of("error", "Branching model settings are only available for Bitbucket repositories");
            }
            BitbucketCloudClientAdapter adapter = (BitbucketCloudClientAdapter) client;
            BitbucketBranchingModelSettings settings = adapter.getUnderlyingClient().updateRepositoryBranchingModelSettings(workspace, repoSlug, development, production, branchTypes);
            return Map.of("branchingModelSettings", settings);
        } catch (IOException e) {
            return Map.of("error", "Failed to update repository branching model settings: " + e.getMessage());
        }
    }

    public Map<String, Object> getEffectiveRepositoryBranchingModel(String workspace, String repoSlug) {
        try {
            VcsMcpClient client = getVcsClient();
            if (!"bitbucket".equals(client.getProviderType())) {
                return Map.of("error", "Branching model is only available for Bitbucket repositories");
            }
            BitbucketCloudClientAdapter adapter = (BitbucketCloudClientAdapter) client;
            BitbucketBranchingModel model = adapter.getUnderlyingClient().getEffectiveRepositoryBranchingModel(workspace, repoSlug);
            return Map.of("effectiveBranchingModel", model);
        } catch (IOException e) {
            return Map.of("error", "Failed to get effective repository branching model: " + e.getMessage());
        }
    }

    public Map<String, Object> getProjectBranchingModel(String workspace, String projectKey) {
        try {
            VcsMcpClient client = getVcsClient();
            if (!"bitbucket".equals(client.getProviderType())) {
                return Map.of("error", "Project branching model is only available for Bitbucket repositories");
            }
            BitbucketCloudClientAdapter adapter = (BitbucketCloudClientAdapter) client;
            BitbucketProjectBranchingModel model = adapter.getUnderlyingClient().getProjectBranchingModel(workspace, projectKey);
            return Map.of("branchingModel", model);
        } catch (IOException e) {
            return Map.of("error", "Failed to get project branching model: " + e.getMessage());
        }
    }

    public Map<String, Object> getProjectBranchingModelSettings(String workspace, String projectKey) {
        try {
            VcsMcpClient client = getVcsClient();
            if (!"bitbucket".equals(client.getProviderType())) {
                return Map.of("error", "Project branching model settings are only available for Bitbucket repositories");
            }
            BitbucketCloudClientAdapter adapter = (BitbucketCloudClientAdapter) client;
            BitbucketProjectBranchingModel settings = adapter.getUnderlyingClient().getProjectBranchingModelSettings(workspace, projectKey);
            return Map.of("branchingModelSettings", settings);
        } catch (IOException e) {
            return Map.of("error", "Failed to get project branching model settings: " + e.getMessage());
        }
    }

    public Map<String, Object> updateProjectBranchingModelSettings(String workspace, String projectKey, Map<String, Object> development, Map<String, Object> production, List<Map<String, Object>> branchTypes) {
        try {
            VcsMcpClient client = getVcsClient();
            if (!"bitbucket".equals(client.getProviderType())) {
                return Map.of("error", "Project branching model settings are only available for Bitbucket repositories");
            }
            BitbucketCloudClientAdapter adapter = (BitbucketCloudClientAdapter) client;
            BitbucketProjectBranchingModel settings = adapter.getUnderlyingClient().updateProjectBranchingModelSettings(workspace, projectKey, development, production, branchTypes);
            return Map.of("branchingModelSettings", settings);
        } catch (IOException e) {
            return Map.of("error", "Failed to update project branching model settings: " + e.getMessage());
        }
    }
}
