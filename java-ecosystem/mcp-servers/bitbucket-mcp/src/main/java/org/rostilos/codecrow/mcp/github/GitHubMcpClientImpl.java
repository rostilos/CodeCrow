package org.rostilos.codecrow.mcp.github;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.mcp.generic.FileDiffInfo;
import org.rostilos.codecrow.mcp.generic.VcsMcpClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class GitHubMcpClientImpl implements VcsMcpClient {

    private static final Logger LOGGER = LoggerFactory.getLogger(GitHubMcpClientImpl.class);
    private static final String API_BASE = "https://api.github.com";
    private static final MediaType JSON = MediaType.parse("application/json");
    private static final Pattern DIFF_FILE_PATTERN = Pattern.compile("^diff --git a/(\\S+) b/(\\S+)");
    
    private final OkHttpClient httpClient;
    private final GitHubConfiguration config;
    private final ObjectMapper objectMapper;
    private final int fileLimit;
    private JsonNode pullRequestCache;

    public GitHubMcpClientImpl(OkHttpClient httpClient, GitHubConfiguration config, int fileLimit) {
        this.httpClient = httpClient;
        this.config = config;
        this.objectMapper = new ObjectMapper();
        this.fileLimit = fileLimit;
    }

    @Override
    public String getProviderType() {
        return "github";
    }

    @Override
    public String getPrNumber() {
        return config.getPrNumber();
    }

    @Override
    public String getPullRequestTitle() throws IOException {
        JsonNode pr = getPullRequestJson();
        return pr.has("title") ? pr.get("title").asText() : "";
    }

    @Override
    public String getPullRequestDescription() throws IOException {
        JsonNode pr = getPullRequestJson();
        return pr.has("body") && !pr.get("body").isNull() ? pr.get("body").asText() : "";
    }

    @Override
    public List<FileDiffInfo> getPullRequestChanges() throws IOException {
        String diff = getPullRequestDiff(config.getOwner(), config.getRepo(), config.getPrNumber());
        List<FileDiffInfo> changes = parseDiff(diff);

        int count = 0;
        for (FileDiffInfo change : changes) {
            if (fileLimit > 0 && count >= fileLimit) break;
            count++;
        }
        
        return changes;
    }

    private List<FileDiffInfo> parseDiff(String rawDiff) {
        List<FileDiffInfo> files = new ArrayList<>();
        if (rawDiff == null || rawDiff.isEmpty()) return files;

        String[] lines = rawDiff.split("\n");
        StringBuilder currentDiff = new StringBuilder();
        String currentFile = null;
        String diffType = "MODIFIED";

        for (String line : lines) {
            Matcher m = DIFF_FILE_PATTERN.matcher(line);
            if (m.find()) {
                if (currentFile != null) {
                    files.add(new FileDiffInfo(currentFile, diffType, null, currentDiff.toString()));
                }
                currentFile = m.group(2);
                currentDiff = new StringBuilder();
                diffType = "MODIFIED";
            }
            
            if (line.startsWith("new file mode")) {
                diffType = "ADDED";
            } else if (line.startsWith("deleted file mode")) {
                diffType = "DELETED";
            }
            
            if (currentFile != null) {
                currentDiff.append(line).append("\n");
            }
        }

        if (currentFile != null) {
            files.add(new FileDiffInfo(currentFile, diffType, null, currentDiff.toString()));
        }

        return files;
    }

    @Override
    public List<Map<String, Object>> listRepositories(String owner, Integer limit) throws IOException {
        int perPage = limit != null ? Math.min(limit, 100) : 30;
        String url = String.format("%s/users/%s/repos?per_page=%d&sort=updated", API_BASE, owner, perPage);
        
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            JsonNode root = parseResponse(resp, "listRepositories");
            List<Map<String, Object>> repos = new ArrayList<>();
            if (root.isArray()) {
                for (JsonNode node : root) {
                    repos.add(parseRepository(node));
                }
            }
            return repos;
        }
    }

    @Override
    public Map<String, Object> getRepository(String owner, String repoSlug) throws IOException {
        String url = String.format("%s/repos/%s/%s", API_BASE, owner, repoSlug);
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            JsonNode node = parseResponse(resp, "getRepository");
            return parseRepository(node);
        }
    }

    @Override
    public List<Map<String, Object>> getPullRequests(String owner, String repoSlug, String state, Integer limit) throws IOException {
        String ghState = state != null ? mapPrState(state) : "open";
        int perPage = limit != null ? Math.min(limit, 100) : 30;
        String url = String.format("%s/repos/%s/%s/pulls?state=%s&per_page=%d", API_BASE, owner, repoSlug, ghState, perPage);
        
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            JsonNode root = parseResponse(resp, "getPullRequests");
            List<Map<String, Object>> prs = new ArrayList<>();
            if (root.isArray()) {
                for (JsonNode node : root) {
                    prs.add(parsePullRequest(node));
                }
            }
            return prs;
        }
    }

    @Override
    public Map<String, Object> createPullRequest(String owner, String repoSlug, String title, String description, 
                                                  String sourceBranch, String targetBranch, List<String> reviewers) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls", API_BASE, owner, repoSlug);
        
        Map<String, Object> body = new HashMap<>();
        body.put("title", title);
        body.put("body", description);
        body.put("head", sourceBranch);
        body.put("base", targetBranch);
        
        Request req = new Request.Builder()
                .url(url)
                .post(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            JsonNode node = parseResponse(resp, "createPullRequest");
            
            if (reviewers != null && !reviewers.isEmpty()) {
                int prNumber = node.get("number").asInt();
                requestReviewers(owner, repoSlug, prNumber, reviewers);
            }
            
            return parsePullRequest(node);
        }
    }

    private void requestReviewers(String owner, String repo, int prNumber, List<String> reviewers) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls/%d/requested_reviewers", API_BASE, owner, repo, prNumber);
        Map<String, Object> body = Map.of("reviewers", reviewers);
        
        Request req = new Request.Builder()
                .url(url)
                .post(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                LOGGER.warn("Failed to request reviewers: {}", resp.code());
            }
        }
    }

    @Override
    public Map<String, Object> getPullRequest(String owner, String repoSlug, String pullRequestId) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls/%s", API_BASE, owner, repoSlug, pullRequestId);
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            return parsePullRequest(parseResponse(resp, "getPullRequest"));
        }
    }

    @Override
    public Map<String, Object> updatePullRequest(String owner, String repoSlug, String pullRequestId, 
                                                  String title, String description) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls/%s", API_BASE, owner, repoSlug, pullRequestId);
        
        Map<String, Object> body = new HashMap<>();
        if (title != null) body.put("title", title);
        if (description != null) body.put("body", description);
        
        Request req = new Request.Builder()
                .url(url)
                .patch(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            return parsePullRequest(parseResponse(resp, "updatePullRequest"));
        }
    }

    @Override
    public Object getPullRequestActivity(String owner, String repoSlug, String pullRequestId) throws IOException {
        String url = String.format("%s/repos/%s/%s/issues/%s/timeline", API_BASE, owner, repoSlug, pullRequestId);
        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github.mockingbird-preview+json")
                .get()
                .build();
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Object approvePullRequest(String owner, String repoSlug, String pullRequestId) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls/%s/reviews", API_BASE, owner, repoSlug, pullRequestId);
        Map<String, Object> body = Map.of("event", "APPROVE");
        
        Request req = new Request.Builder()
                .url(url)
                .post(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Object unapprovePullRequest(String owner, String repoSlug, String pullRequestId) throws IOException {
        return Map.of("message", "GitHub does not support unapproving. Use dismiss review instead.");
    }

    @Override
    public Object declinePullRequest(String owner, String repoSlug, String pullRequestId, String message) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls/%s", API_BASE, owner, repoSlug, pullRequestId);
        Map<String, Object> body = Map.of("state", "closed");
        
        Request req = new Request.Builder()
                .url(url)
                .patch(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Object mergePullRequest(String owner, String repoSlug, String pullRequestId, String message, String strategy) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls/%s/merge", API_BASE, owner, repoSlug, pullRequestId);
        
        Map<String, Object> body = new HashMap<>();
        if (message != null) body.put("commit_message", message);
        if (strategy != null) body.put("merge_method", mapMergeStrategy(strategy));
        
        Request req = new Request.Builder()
                .url(url)
                .put(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Object getPullRequestComments(String owner, String repoSlug, String pullRequestId) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls/%s/comments", API_BASE, owner, repoSlug, pullRequestId);
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public String getPullRequestDiff(String owner, String repoSlug, String pullRequestId) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls/%s", API_BASE, owner, repoSlug, pullRequestId);
        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github.diff")
                .get()
                .build();
        try (Response resp = httpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                throw new IOException("Failed to get PR diff: " + resp.code());
            }
            return resp.body().string();
        }
    }

    @Override
    public Object getPullRequestCommits(String owner, String repoSlug, String pullRequestId) throws IOException {
        String url = String.format("%s/repos/%s/%s/pulls/%s/commits", API_BASE, owner, repoSlug, pullRequestId);
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Map<String, Object> getBranchingModel(String owner, String repoSlug) throws IOException {
        return Map.of(
                "message", "GitHub does not have a native branching model concept",
                "default_branch", getDefaultBranch(owner, repoSlug)
        );
    }

    @Override
    public Map<String, Object> getBranchingModelSettings(String owner, String repoSlug) throws IOException {
        return getBranchingModel(owner, repoSlug);
    }

    @Override
    public Map<String, Object> updateBranchingModelSettings(String owner, String repoSlug, 
                                                             Map<String, Object> development, 
                                                             Map<String, Object> production, 
                                                             List<Map<String, Object>> branchTypes) throws IOException {
        return Map.of("message", "GitHub does not support branching model configuration via API");
    }

    @Override
    public String getBranchFileContent(String owner, String repoSlug, String branch, String filePath) throws IOException {
        String encodedPath = URLEncoder.encode(filePath, StandardCharsets.UTF_8).replace("+", "%20");
        String url = String.format("%s/repos/%s/%s/contents/%s?ref=%s", 
                API_BASE, owner, repoSlug, encodedPath, URLEncoder.encode(branch, StandardCharsets.UTF_8));
        
        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github.raw+json")
                .get()
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                if (resp.code() == 404) {
                    return "File not found: " + filePath;
                }
                throw new IOException("Failed to get file content: " + resp.code());
            }
            return resp.body().string();
        }
    }

    @Override
    public String getRootDirectory(String owner, String repoSlug, String branch) throws IOException {
        return getDirectoryByPath(owner, repoSlug, branch, "");
    }

    @Override
    public String getDirectoryByPath(String owner, String repoSlug, String branch, String dirPath) throws IOException {
        String path = dirPath == null || dirPath.isEmpty() ? "" : "/" + dirPath;
        String url = String.format("%s/repos/%s/%s/contents%s?ref=%s", 
                API_BASE, owner, repoSlug, path, URLEncoder.encode(branch, StandardCharsets.UTF_8));
        
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                throw new IOException("Failed to get directory: " + resp.code());
            }
            return resp.body().string();
        }
    }

    private JsonNode getPullRequestJson() throws IOException {
        if (pullRequestCache != null) return pullRequestCache;
        
        String url = String.format("%s/repos/%s/%s/pulls/%s", API_BASE, config.getOwner(), config.getRepo(), config.getPrNumber());
        Request req = new Request.Builder().url(url).get().build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            pullRequestCache = parseResponse(resp, "getPullRequestJson");
            return pullRequestCache;
        }
    }

    private String getDefaultBranch(String owner, String repo) throws IOException {
        Map<String, Object> repoInfo = getRepository(owner, repo);
        return (String) repoInfo.getOrDefault("default_branch", "main");
    }

    private JsonNode parseResponse(Response resp, String operation) throws IOException {
        if (!resp.isSuccessful()) {
            String body = resp.body() != null ? resp.body().string() : "";
            throw new GitHubException(String.format("%s failed: %d - %s", operation, resp.code(), body));
        }
        return objectMapper.readTree(resp.body().string());
    }

    private Map<String, Object> parseRepository(JsonNode node) {
        Map<String, Object> repo = new HashMap<>();
        repo.put("id", node.get("id").asLong());
        repo.put("name", getTextOrNull(node, "name"));
        repo.put("full_name", getTextOrNull(node, "full_name"));
        repo.put("description", getTextOrNull(node, "description"));
        repo.put("private", node.path("private").asBoolean());
        repo.put("default_branch", getTextOrNull(node, "default_branch"));
        repo.put("html_url", getTextOrNull(node, "html_url"));
        repo.put("clone_url", getTextOrNull(node, "clone_url"));
        return repo;
    }

    private Map<String, Object> parsePullRequest(JsonNode node) {
        Map<String, Object> pr = new HashMap<>();
        pr.put("id", node.get("id").asLong());
        pr.put("number", node.get("number").asInt());
        pr.put("title", getTextOrNull(node, "title"));
        pr.put("description", getTextOrNull(node, "body"));
        pr.put("state", getTextOrNull(node, "state"));
        pr.put("html_url", getTextOrNull(node, "html_url"));
        pr.put("source_branch", node.path("head").path("ref").asText());
        pr.put("target_branch", node.path("base").path("ref").asText());
        pr.put("author", node.path("user").path("login").asText());
        pr.put("created_on", getTextOrNull(node, "created_at"));
        pr.put("updated_on", getTextOrNull(node, "updated_at"));
        pr.put("merged", node.path("merged").asBoolean());
        return pr;
    }

    private String getTextOrNull(JsonNode node, String field) {
        return node.has(field) && !node.get(field).isNull() ? node.get(field).asText() : null;
    }

    private String mapPrState(String state) {
        return switch (state.toUpperCase()) {
            case "OPEN", "OPENED" -> "open";
            case "MERGED", "CLOSED", "DECLINED" -> "closed";
            default -> "all";
        };
    }

    private String mapMergeStrategy(String strategy) {
        return switch (strategy.toLowerCase()) {
            case "squash" -> "squash";
            case "rebase", "fast-forward" -> "rebase";
            default -> "merge";
        };
    }
}
