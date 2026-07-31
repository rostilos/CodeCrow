package org.rostilos.codecrow.mcp.gitlab;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import org.rostilos.codecrow.mcp.generic.FileDiffInfo;
import org.rostilos.codecrow.mcp.generic.VcsMcpClient;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabClient;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequest;
import org.rostilos.codecrow.vcsclient.model.VcsRepository;
import org.rostilos.codecrow.vcsclient.model.VcsRepositoryPage;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * MCP shape adapter for the shared GitLab client.
 *
 * <p>This class contains no GitLab endpoints or HTTP behavior. The MCP process
 * has to construct its own authorized client because it runs outside the main
 * JVM, but all provider operations are delegated to {@code vcs-client}.</p>
 */
public class GitLabMcpClientImpl implements VcsMcpClient {

    private static final Pattern DIFF_FILE_PATTERN =
            Pattern.compile("^diff --git a/(\\S+) b/(\\S+)");

    private final GitLabClient client;
    private final GitLabConfiguration config;
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final int fileLimit;
    private VcsPullRequest pullRequestCache;

    public GitLabMcpClientImpl(
            OkHttpClient httpClient,
            GitLabConfiguration config,
            int fileLimit
    ) {
        this(new GitLabClient(httpClient, config.getBaseUrl()), config, fileLimit);
    }

    public GitLabMcpClientImpl(
            GitLabClient client,
            GitLabConfiguration config,
            int fileLimit
    ) {
        this.client = client;
        this.config = config;
        this.fileLimit = fileLimit;
    }

    @Override
    public String getProviderType() {
        return "gitlab";
    }

    @Override
    public String getPrNumber() {
        return config.getMrIid();
    }

    @Override
    public String getPullRequestTitle() throws IOException {
        return currentPullRequest().title() != null ? currentPullRequest().title() : "";
    }

    @Override
    public String getPullRequestDescription() throws IOException {
        return currentPullRequest().description() != null
                ? currentPullRequest().description()
                : "";
    }

    @Override
    public List<FileDiffInfo> getPullRequestChanges() throws IOException {
        List<FileDiffInfo> changes = parseDiff(getPullRequestDiff(
                config.getNamespace(), config.getProject(), config.getMrIid()));
        if (fileLimit > 0 && changes.size() > fileLimit) {
            return List.copyOf(changes.subList(0, fileLimit));
        }
        return changes;
    }

    @Override
    public List<Map<String, Object>> listRepositories(
            String namespace,
            Integer limit
    ) throws IOException {
        int requested = limit != null ? Math.max(0, limit) : 20;
        List<Map<String, Object>> repositories = new ArrayList<>();
        int pageNumber = 1;
        while (repositories.size() < requested) {
            VcsRepositoryPage page = client.listRepositories(namespace, pageNumber++);
            for (VcsRepository repository : page.items()) {
                repositories.add(toRepositoryMap(repository));
                if (repositories.size() >= requested) {
                    break;
                }
            }
            if (!page.hasNext() || page.items().isEmpty()) {
                break;
            }
        }
        return repositories;
    }

    @Override
    public Map<String, Object> getRepository(
            String namespace,
            String projectSlug
    ) throws IOException {
        VcsRepository repository = client.getRepository(namespace, projectSlug);
        return repository != null ? toRepositoryMap(repository) : Map.of();
    }

    @Override
    public List<Map<String, Object>> getPullRequests(
            String namespace,
            String projectSlug,
            String state,
            Integer limit
    ) throws IOException {
        JsonNode result = client.listMergeRequests(
                namespace, projectSlug, state, limit != null ? limit : 20);
        List<Map<String, Object>> pullRequests = new ArrayList<>();
        if (result.isArray()) {
            for (JsonNode mergeRequest : result) {
                pullRequests.add(toPullRequestMap(mergeRequest));
            }
        }
        return pullRequests;
    }

    @Override
    public Map<String, Object> createPullRequest(
            String namespace,
            String projectSlug,
            String title,
            String description,
            String sourceBranch,
            String targetBranch,
            List<String> reviewers
    ) throws IOException {
        return toPullRequestMap(client.createMergeRequest(
                namespace, projectSlug, title, description,
                sourceBranch, targetBranch, reviewers));
    }

    @Override
    public Map<String, Object> getPullRequest(
            String namespace,
            String projectSlug,
            String pullRequestId
    ) throws IOException {
        return toPullRequestMap(client.getMergeRequest(
                namespace, projectSlug, parseId(pullRequestId)));
    }

    @Override
    public Map<String, Object> updatePullRequest(
            String namespace,
            String projectSlug,
            String pullRequestId,
            String title,
            String description
    ) throws IOException {
        return toPullRequestMap(client.updateMergeRequest(
                namespace, projectSlug, parseId(pullRequestId), title, description));
    }

    @Override
    public Object getPullRequestActivity(
            String namespace,
            String projectSlug,
            String pullRequestId
    ) throws IOException {
        return toObject(client.getMergeRequestActivity(
                namespace, projectSlug, parseId(pullRequestId)));
    }

    @Override
    public Object approvePullRequest(
            String namespace,
            String projectSlug,
            String pullRequestId
    ) throws IOException {
        return toObject(client.approveMergeRequest(
                namespace, projectSlug, parseId(pullRequestId)));
    }

    @Override
    public Object unapprovePullRequest(
            String namespace,
            String projectSlug,
            String pullRequestId
    ) throws IOException {
        return toObject(client.unapproveMergeRequest(
                namespace, projectSlug, parseId(pullRequestId)));
    }

    @Override
    public Object declinePullRequest(
            String namespace,
            String projectSlug,
            String pullRequestId,
            String message
    ) throws IOException {
        return toObject(client.closeMergeRequest(
                namespace, projectSlug, parseId(pullRequestId)));
    }

    @Override
    public Object mergePullRequest(
            String namespace,
            String projectSlug,
            String pullRequestId,
            String message,
            String strategy
    ) throws IOException {
        return toObject(client.mergeMergeRequest(
                namespace, projectSlug, parseId(pullRequestId), message, strategy));
    }

    @Override
    public Object getPullRequestComments(
            String namespace,
            String projectSlug,
            String pullRequestId
    ) throws IOException {
        return toObject(client.getMergeRequestNotes(
                namespace, projectSlug, parseId(pullRequestId)));
    }

    @Override
    public String getPullRequestDiff(
            String namespace,
            String projectSlug,
            String pullRequestId
    ) throws IOException {
        return client.getPullRequestDiff(
                namespace, projectSlug, parseId(pullRequestId));
    }

    @Override
    public Object getPullRequestCommits(
            String namespace,
            String projectSlug,
            String pullRequestId
    ) throws IOException {
        return toObject(client.getMergeRequestCommits(
                namespace, projectSlug, parseId(pullRequestId)));
    }

    @Override
    public Map<String, Object> getBranchingModel(
            String namespace,
            String projectSlug
    ) throws IOException {
        return Map.of(
                "message", "GitLab does not have a native branching model concept",
                "default_branch", client.getDefaultBranch(namespace, projectSlug));
    }

    @Override
    public Map<String, Object> getBranchingModelSettings(
            String namespace,
            String projectSlug
    ) throws IOException {
        return getBranchingModel(namespace, projectSlug);
    }

    @Override
    public Map<String, Object> updateBranchingModelSettings(
            String namespace,
            String projectSlug,
            Map<String, Object> development,
            Map<String, Object> production,
            List<Map<String, Object>> branchTypes
    ) {
        return Map.of("message", "GitLab does not support branching model configuration via API");
    }

    @Override
    public String getBranchFileContent(
            String namespace,
            String projectSlug,
            String branch,
            String filePath
    ) throws IOException {
        String content = client.getFileContent(namespace, projectSlug, filePath, branch);
        return content != null ? content : "File not found: " + filePath;
    }

    @Override
    public String getRootDirectory(
            String namespace,
            String projectSlug,
            String branch
    ) throws IOException {
        return getDirectoryByPath(namespace, projectSlug, branch, "");
    }

    @Override
    public String getDirectoryByPath(
            String namespace,
            String projectSlug,
            String branch,
            String dirPath
    ) throws IOException {
        return client.getRepositoryTree(namespace, projectSlug, branch, dirPath);
    }

    private VcsPullRequest currentPullRequest() throws IOException {
        if (pullRequestCache == null) {
            pullRequestCache = client.getPullRequest(
                    config.getNamespace(), config.getProject(), parseId(config.getMrIid()));
        }
        return pullRequestCache;
    }

    private List<FileDiffInfo> parseDiff(String rawDiff) {
        List<FileDiffInfo> files = new ArrayList<>();
        if (rawDiff == null || rawDiff.isEmpty()) {
            return files;
        }

        StringBuilder currentDiff = new StringBuilder();
        String currentFile = null;
        String diffType = "MODIFIED";
        for (String line : rawDiff.split("\n")) {
            Matcher matcher = DIFF_FILE_PATTERN.matcher(line);
            if (matcher.find()) {
                if (currentFile != null) {
                    files.add(new FileDiffInfo(
                            currentFile, diffType, null, currentDiff.toString()));
                }
                currentFile = matcher.group(2);
                currentDiff = new StringBuilder();
                diffType = "MODIFIED";
            }
            if (line.startsWith("new file mode")) {
                diffType = "ADDED";
            } else if (line.startsWith("deleted file mode")) {
                diffType = "DELETED";
            }
            if (currentFile != null) {
                currentDiff.append(line).append('\n');
            }
        }
        if (currentFile != null) {
            files.add(new FileDiffInfo(
                    currentFile, diffType, null, currentDiff.toString()));
        }
        return files;
    }

    private Map<String, Object> toRepositoryMap(VcsRepository repository) {
        Map<String, Object> result = new HashMap<>();
        result.put("id", repository.id());
        result.put("name", repository.name());
        result.put("path", repository.slug());
        result.put("path_with_namespace", repository.fullName());
        result.put("full_name", repository.fullName());
        result.put("description", repository.description());
        result.put("private", repository.isPrivate());
        result.put("default_branch", repository.defaultBranch());
        result.put("web_url", repository.htmlUrl());
        result.put("html_url", repository.htmlUrl());
        result.put("http_url_to_repo", repository.cloneUrl());
        result.put("clone_url", repository.cloneUrl());
        return result;
    }

    private Map<String, Object> toPullRequestMap(JsonNode node) {
        Map<String, Object> result = new HashMap<>();
        result.put("id", node.path("id").asLong());
        result.put("iid", node.path("iid").asInt());
        result.put("number", node.path("iid").asInt());
        result.put("title", text(node, "title"));
        result.put("description", text(node, "description"));
        result.put("state", text(node, "state"));
        result.put("web_url", text(node, "web_url"));
        result.put("html_url", text(node, "web_url"));
        result.put("source_branch", text(node, "source_branch"));
        result.put("target_branch", text(node, "target_branch"));
        result.put("author", node.path("author").path("username").asText(null));
        result.put("created_on", text(node, "created_at"));
        result.put("updated_on", text(node, "updated_at"));
        result.put("merged", "merged".equals(text(node, "state")));
        return result;
    }

    private Object toObject(JsonNode node) {
        return objectMapper.convertValue(node, Object.class);
    }

    private String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value != null && !value.isNull() ? value.asText() : null;
    }

    private long parseId(String value) {
        return Long.parseLong(value);
    }
}
