package org.rostilos.codecrow.mcp.gitlab;

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

/**
 * GitLab implementation of VcsMcpClient.
 * Handles GitLab-specific API interactions for MCP tools.
 */
public class GitLabMcpClientImpl implements VcsMcpClient {

    private static final Logger LOGGER = LoggerFactory.getLogger(GitLabMcpClientImpl.class);
    private static final String API_BASE = "https://gitlab.com/api/v4";
    private static final MediaType JSON = MediaType.parse("application/json");
    private static final Pattern DIFF_FILE_PATTERN = Pattern.compile("^diff --git a/(\\S+) b/(\\S+)");
    
    private final OkHttpClient httpClient;
    private final GitLabConfiguration config;
    private final ObjectMapper objectMapper;
    private final int fileLimit;
    private JsonNode mergeRequestCache;

    public GitLabMcpClientImpl(OkHttpClient httpClient, GitLabConfiguration config, int fileLimit) {
        this.httpClient = httpClient;
        this.config = config;
        this.objectMapper = new ObjectMapper();
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
        JsonNode mr = getMergeRequestJson();
        return mr.has("title") ? mr.get("title").asText() : "";
    }

    @Override
    public String getPullRequestDescription() throws IOException {
        JsonNode mr = getMergeRequestJson();
        return mr.has("description") && !mr.get("description").isNull() ? mr.get("description").asText() : "";
    }

    @Override
    public List<FileDiffInfo> getPullRequestChanges() throws IOException {
        String diff = getMergeRequestDiff(config.getNamespace(), config.getProject(), config.getMrIid());
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
    public List<Map<String, Object>> listRepositories(String namespace, Integer limit) throws IOException {
        int perPage = limit != null ? Math.min(limit, 100) : 20;
        String encodedNamespace = URLEncoder.encode(namespace, StandardCharsets.UTF_8);
        
        // First try as group
        String url = String.format("%s/groups/%s/projects?per_page=%d&order_by=updated_at&sort=desc", 
                API_BASE, encodedNamespace, perPage);
        
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            if (resp.isSuccessful()) {
                JsonNode root = objectMapper.readTree(resp.body().string());
                List<Map<String, Object>> repos = new ArrayList<>();
                if (root.isArray()) {
                    for (JsonNode node : root) {
                        repos.add(parseRepository(node));
                    }
                }
                return repos;
            }
        }
        
        // Fallback to user projects
        url = String.format("%s/users/%s/projects?per_page=%d&order_by=updated_at&sort=desc", 
                API_BASE, encodedNamespace, perPage);
        req = new Request.Builder().url(url).get().build();
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
    public Map<String, Object> getRepository(String namespace, String projectSlug) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s", API_BASE, encodedPath);
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            JsonNode node = parseResponse(resp, "getRepository");
            return parseRepository(node);
        }
    }

    @Override
    public List<Map<String, Object>> getPullRequests(String namespace, String projectSlug, String state, Integer limit) throws IOException {
        String gitlabState = state != null ? mapMrState(state) : "opened";
        int perPage = limit != null ? Math.min(limit, 100) : 20;
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests?state=%s&per_page=%d", 
                API_BASE, encodedPath, gitlabState, perPage);
        
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            JsonNode root = parseResponse(resp, "getPullRequests");
            List<Map<String, Object>> mrs = new ArrayList<>();
            if (root.isArray()) {
                for (JsonNode node : root) {
                    mrs.add(parseMergeRequest(node));
                }
            }
            return mrs;
        }
    }

    @Override
    public Map<String, Object> createPullRequest(String namespace, String projectSlug, String title, String description, 
                                                  String sourceBranch, String targetBranch, List<String> reviewers) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests", API_BASE, encodedPath);
        
        Map<String, Object> body = new HashMap<>();
        body.put("title", title);
        body.put("description", description);
        body.put("source_branch", sourceBranch);
        body.put("target_branch", targetBranch);
        
        if (reviewers != null && !reviewers.isEmpty()) {
            body.put("reviewer_ids", reviewers);
        }
        
        Request req = new Request.Builder()
                .url(url)
                .post(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            JsonNode node = parseResponse(resp, "createPullRequest");
            return parseMergeRequest(node);
        }
    }

    @Override
    public Map<String, Object> getPullRequest(String namespace, String projectSlug, String mrIid) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s", API_BASE, encodedPath, mrIid);
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            return parseMergeRequest(parseResponse(resp, "getPullRequest"));
        }
    }

    @Override
    public Map<String, Object> updatePullRequest(String namespace, String projectSlug, String mrIid, 
                                                  String title, String description) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s", API_BASE, encodedPath, mrIid);
        
        Map<String, Object> body = new HashMap<>();
        if (title != null) body.put("title", title);
        if (description != null) body.put("description", description);
        
        Request req = new Request.Builder()
                .url(url)
                .put(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            return parseMergeRequest(parseResponse(resp, "updatePullRequest"));
        }
    }

    @Override
    public Object getPullRequestActivity(String namespace, String projectSlug, String mrIid) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s/resource_state_events", 
                API_BASE, encodedPath, mrIid);
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Object approvePullRequest(String namespace, String projectSlug, String mrIid) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s/approve", API_BASE, encodedPath, mrIid);
        
        Request req = new Request.Builder()
                .url(url)
                .post(RequestBody.create("{}", JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Object unapprovePullRequest(String namespace, String projectSlug, String mrIid) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s/unapprove", API_BASE, encodedPath, mrIid);
        
        Request req = new Request.Builder()
                .url(url)
                .post(RequestBody.create("{}", JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Object declinePullRequest(String namespace, String projectSlug, String mrIid, String message) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s", API_BASE, encodedPath, mrIid);
        
        Map<String, Object> body = Map.of("state_event", "close");
        
        Request req = new Request.Builder()
                .url(url)
                .put(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Object mergePullRequest(String namespace, String projectSlug, String mrIid, String message, String strategy) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s/merge", API_BASE, encodedPath, mrIid);
        
        Map<String, Object> body = new HashMap<>();
        if (message != null) body.put("merge_commit_message", message);
        if (strategy != null) {
            if ("squash".equalsIgnoreCase(strategy)) {
                body.put("squash", true);
            }
        }
        
        Request req = new Request.Builder()
                .url(url)
                .put(RequestBody.create(objectMapper.writeValueAsString(body), JSON))
                .build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Object getPullRequestComments(String namespace, String projectSlug, String mrIid) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s/notes", API_BASE, encodedPath, mrIid);
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public String getPullRequestDiff(String namespace, String projectSlug, String mrIid) throws IOException {
        return getMergeRequestDiff(namespace, projectSlug, mrIid);
    }

    private String getMergeRequestDiff(String namespace, String project, String mrIid) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s/changes", API_BASE, encodedPath, mrIid);
        
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                throw new IOException("Failed to get MR diff: " + resp.code() + " - " + body);
            }
            
            String responseBody = resp.body() != null ? resp.body().string() : "{}";
            return buildUnifiedDiff(responseBody);
        }
    }

    private String buildUnifiedDiff(String responseBody) throws IOException {
        StringBuilder combinedDiff = new StringBuilder();
        JsonNode root = objectMapper.readTree(responseBody);
        JsonNode changes = root.get("changes");
        
        if (changes == null || !changes.isArray()) {
            return "";
        }
        
        int fileCount = 0;
        for (JsonNode change : changes) {
            if (fileLimit > 0 && fileCount >= fileLimit) {
                break;
            }
            fileCount++;
            
            String oldPath = change.has("old_path") ? change.get("old_path").asText() : "";
            String newPath = change.has("new_path") ? change.get("new_path").asText() : "";
            String diff = change.has("diff") ? change.get("diff").asText() : "";
            boolean newFile = change.has("new_file") && change.get("new_file").asBoolean();
            boolean deletedFile = change.has("deleted_file") && change.get("deleted_file").asBoolean();
            boolean renamedFile = change.has("renamed_file") && change.get("renamed_file").asBoolean();
            
            String fromFile = renamedFile ? oldPath : newPath;
            combinedDiff.append("diff --git a/").append(fromFile).append(" b/").append(newPath).append("\n");
            
            if (newFile) {
                combinedDiff.append("new file mode 100644\n");
                combinedDiff.append("--- /dev/null\n");
                combinedDiff.append("+++ b/").append(newPath).append("\n");
            } else if (deletedFile) {
                combinedDiff.append("deleted file mode 100644\n");
                combinedDiff.append("--- a/").append(oldPath).append("\n");
                combinedDiff.append("+++ /dev/null\n");
            } else if (renamedFile) {
                combinedDiff.append("rename from ").append(oldPath).append("\n");
                combinedDiff.append("rename to ").append(newPath).append("\n");
                combinedDiff.append("--- a/").append(oldPath).append("\n");
                combinedDiff.append("+++ b/").append(newPath).append("\n");
            } else {
                combinedDiff.append("--- a/").append(oldPath).append("\n");
                combinedDiff.append("+++ b/").append(newPath).append("\n");
            }
            
            if (!diff.isEmpty()) {
                combinedDiff.append(diff);
                if (!diff.endsWith("\n")) {
                    combinedDiff.append("\n");
                }
            }
            
            combinedDiff.append("\n");
        }
        
        return combinedDiff.toString();
    }

    @Override
    public Object getPullRequestCommits(String namespace, String projectSlug, String mrIid) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s/commits", API_BASE, encodedPath, mrIid);
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            return objectMapper.readValue(resp.body().string(), Object.class);
        }
    }

    @Override
    public Map<String, Object> getBranchingModel(String namespace, String projectSlug) throws IOException {
        return Map.of(
                "message", "GitLab does not have a native branching model concept",
                "default_branch", getDefaultBranch(namespace, projectSlug)
        );
    }

    @Override
    public Map<String, Object> getBranchingModelSettings(String namespace, String projectSlug) throws IOException {
        return getBranchingModel(namespace, projectSlug);
    }

    @Override
    public Map<String, Object> updateBranchingModelSettings(String namespace, String projectSlug, 
                                                             Map<String, Object> development, 
                                                             Map<String, Object> production, 
                                                             List<Map<String, Object>> branchTypes) throws IOException {
        return Map.of("message", "GitLab does not support branching model configuration via API");
    }

    @Override
    public String getBranchFileContent(String namespace, String projectSlug, String branch, String filePath) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedProjectPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String encodedFilePath = URLEncoder.encode(filePath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/repository/files/%s/raw?ref=%s", 
                API_BASE, encodedProjectPath, encodedFilePath, URLEncoder.encode(branch, StandardCharsets.UTF_8));
        
        Request req = new Request.Builder().url(url).get().build();
        
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
    public String getRootDirectory(String namespace, String projectSlug, String branch) throws IOException {
        return getDirectoryByPath(namespace, projectSlug, branch, "");
    }

    @Override
    public String getDirectoryByPath(String namespace, String projectSlug, String branch, String dirPath) throws IOException {
        String projectPath = namespace + "/" + projectSlug;
        String encodedProjectPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String path = dirPath == null || dirPath.isEmpty() ? "" : "&path=" + URLEncoder.encode(dirPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/repository/tree?ref=%s%s", 
                API_BASE, encodedProjectPath, URLEncoder.encode(branch, StandardCharsets.UTF_8), path);
        
        Request req = new Request.Builder().url(url).get().build();
        try (Response resp = httpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                throw new IOException("Failed to get directory: " + resp.code());
            }
            return resp.body().string();
        }
    }

    private JsonNode getMergeRequestJson() throws IOException {
        if (mergeRequestCache != null) return mergeRequestCache;
        
        String projectPath = config.getNamespace() + "/" + config.getProject();
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = String.format("%s/projects/%s/merge_requests/%s", API_BASE, encodedPath, config.getMrIid());
        Request req = new Request.Builder().url(url).get().build();
        
        try (Response resp = httpClient.newCall(req).execute()) {
            mergeRequestCache = parseResponse(resp, "getMergeRequestJson");
            return mergeRequestCache;
        }
    }

    private String getDefaultBranch(String namespace, String project) throws IOException {
        Map<String, Object> repoInfo = getRepository(namespace, project);
        return (String) repoInfo.getOrDefault("default_branch", "main");
    }

    private JsonNode parseResponse(Response resp, String operation) throws IOException {
        if (!resp.isSuccessful()) {
            String body = resp.body() != null ? resp.body().string() : "";
            throw new GitLabException(String.format("%s failed: %d - %s", operation, resp.code(), body));
        }
        return objectMapper.readTree(resp.body().string());
    }

    private Map<String, Object> parseRepository(JsonNode node) {
        Map<String, Object> repo = new HashMap<>();
        repo.put("id", node.get("id").asLong());
        repo.put("name", getTextOrNull(node, "name"));
        repo.put("path", getTextOrNull(node, "path"));
        repo.put("path_with_namespace", getTextOrNull(node, "path_with_namespace"));
        repo.put("full_name", getTextOrNull(node, "path_with_namespace"));
        repo.put("description", getTextOrNull(node, "description"));
        repo.put("private", !"public".equals(getTextOrNull(node, "visibility")));
        repo.put("default_branch", getTextOrNull(node, "default_branch"));
        repo.put("web_url", getTextOrNull(node, "web_url"));
        repo.put("html_url", getTextOrNull(node, "web_url"));
        repo.put("http_url_to_repo", getTextOrNull(node, "http_url_to_repo"));
        repo.put("clone_url", getTextOrNull(node, "http_url_to_repo"));
        return repo;
    }

    private Map<String, Object> parseMergeRequest(JsonNode node) {
        Map<String, Object> mr = new HashMap<>();
        mr.put("id", node.get("id").asLong());
        mr.put("iid", node.get("iid").asInt());
        mr.put("number", node.get("iid").asInt());
        mr.put("title", getTextOrNull(node, "title"));
        mr.put("description", getTextOrNull(node, "description"));
        mr.put("state", getTextOrNull(node, "state"));
        mr.put("web_url", getTextOrNull(node, "web_url"));
        mr.put("html_url", getTextOrNull(node, "web_url"));
        mr.put("source_branch", getTextOrNull(node, "source_branch"));
        mr.put("target_branch", getTextOrNull(node, "target_branch"));
        mr.put("author", node.has("author") ? node.get("author").get("username").asText() : null);
        mr.put("created_on", getTextOrNull(node, "created_at"));
        mr.put("updated_on", getTextOrNull(node, "updated_at"));
        mr.put("merged", "merged".equals(getTextOrNull(node, "state")));
        return mr;
    }

    private String getTextOrNull(JsonNode node, String field) {
        return node.has(field) && !node.get(field).isNull() ? node.get(field).asText() : null;
    }

    private String mapMrState(String state) {
        return switch (state.toUpperCase()) {
            case "OPEN", "OPENED" -> "opened";
            case "MERGED" -> "merged";
            case "CLOSED", "DECLINED" -> "closed";
            default -> "all";
        };
    }
}
