package org.rostilos.codecrow.vcsclient.gitlab;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.model.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * VcsClient implementation for GitLab.
 * Supports OAuth token-based connections.
 */
public class GitLabClient implements VcsClient {
    
    private static final Logger log = LoggerFactory.getLogger(GitLabClient.class);
    
    private static final String API_BASE = GitLabConfig.API_BASE;
    private static final int DEFAULT_PAGE_SIZE = GitLabConfig.DEFAULT_PAGE_SIZE;
    private static final MediaType JSON_MEDIA_TYPE = MediaType.parse("application/json");

    private static final String ACCEPT_HEADER = "Accept";
    private static final String GITLAB_ACCEPT_HEADER = "application/json";

    private final OkHttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final String baseUrl;
    
    public GitLabClient(OkHttpClient httpClient) {
        this(httpClient, API_BASE);
    }
    
    public GitLabClient(OkHttpClient httpClient, String baseUrl) {
        this.httpClient = httpClient;
        this.objectMapper = new ObjectMapper();
        this.baseUrl = baseUrl != null ? baseUrl : API_BASE;
    }
    
    @Override
    public boolean validateConnection() throws IOException {
        Request request = createGetRequest(baseUrl + "/user");
        try (Response response = httpClient.newCall(request).execute()) {
            return response.isSuccessful();
        }
    }
    
    @Override
    public List<VcsWorkspace> listWorkspaces() throws IOException {
        List<VcsWorkspace> workspaces = new ArrayList<>();
        
        // Add user's personal namespace as a "workspace"
        VcsUser currentUser = getCurrentUser();
        if (currentUser != null) {
            workspaces.add(new VcsWorkspace(
                    currentUser.id(),
                    currentUser.username(),
                    currentUser.displayName() != null ? currentUser.displayName() : currentUser.username(),
                    false,
                    currentUser.avatarUrl(),
                    currentUser.htmlUrl()
            ));
        }
        
        // GitLab uses groups instead of organizations
        int page = 1;
        while (true) {
            String url = baseUrl + "/groups?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page + "&min_access_level=10";
            
            Request request = createGetRequest(url);
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw createException("list groups", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                if (!root.isArray() || root.isEmpty()) {
                    break;
                }
                
                for (JsonNode node : root) {
                    workspaces.add(parseGroup(node));
                }
                
                // Check for pagination via headers
                String nextPage = response.header("X-Next-Page");
                if (nextPage == null || nextPage.isBlank()) {
                    break;
                }
                page++;
            }
        }
        
        return workspaces;
    }
    
    @Override
    public VcsRepositoryPage listRepositories(String workspaceId, int page) throws IOException {
        String url;
        String sortParams = "&order_by=updated_at&sort=desc";
        
        // Check if workspaceId is a group or user
        if (isCurrentUser(workspaceId)) {
            url = baseUrl + "/projects?membership=true&per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page + sortParams;
        } else {
            // Try as group first
            String encodedWorkspace = URLEncoder.encode(workspaceId, StandardCharsets.UTF_8);
            url = baseUrl + "/groups/" + encodedWorkspace + "/projects?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page + sortParams;
        }
        
        return fetchRepositoryPage(url, workspaceId, page);
    }
    
    @Override
    public VcsRepositoryPage searchRepositories(String workspaceId, String query, int page) throws IOException {
        String encodedQuery = URLEncoder.encode(query, StandardCharsets.UTF_8);
        
        String url;
        if (isCurrentUser(workspaceId)) {
            url = baseUrl + "/projects?search=" + encodedQuery + "&membership=true&per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
        } else {
            String encodedWorkspace = URLEncoder.encode(workspaceId, StandardCharsets.UTF_8);
            url = baseUrl + "/groups/" + encodedWorkspace + "/projects?search=" + encodedQuery + "&per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
        }
        
        return fetchRepositoryPage(url, workspaceId, page);
    }
    
    @Override
    public VcsRepository getRepository(String workspaceId, String repoIdOrSlug) throws IOException {
        // GitLab uses project ID or URL-encoded path
        // If workspaceId is empty or null, repoIdOrSlug contains the full path (e.g., "namespace/repo")
        String projectPath;
        String effectiveNamespace;
        if (workspaceId == null || workspaceId.isBlank()) {
            projectPath = repoIdOrSlug;
            // Extract namespace from full path for parseRepository
            effectiveNamespace = repoIdOrSlug.contains("/") 
                    ? repoIdOrSlug.substring(0, repoIdOrSlug.lastIndexOf("/"))
                    : repoIdOrSlug;
        } else {
            projectPath = workspaceId + "/" + repoIdOrSlug;
            effectiveNamespace = workspaceId;
        }
        
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = baseUrl + "/projects/" + encodedPath;

        Request request = createGetRequest(url);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                if (response.code() == 404) {
                    // Try with just the repo ID (might be a numeric ID)
                    url = baseUrl + "/projects/" + URLEncoder.encode(repoIdOrSlug, StandardCharsets.UTF_8);
                    Request retryRequest = createGetRequest(url);
                    try (Response retryResponse = httpClient.newCall(retryRequest).execute()) {
                        if (!retryResponse.isSuccessful()) {
                            if (retryResponse.code() == 404) {
                                return null;
                            }
                            throw createException("get repository", retryResponse);
                        }
                        JsonNode node = objectMapper.readTree(retryResponse.body().string());
                        return parseRepository(node, effectiveNamespace);
                    }
                }
                throw createException("get repository", response);
            }
            
            JsonNode node = objectMapper.readTree(response.body().string());
            return parseRepository(node, effectiveNamespace);
        }
    }
    
    @Override
    public String ensureWebhook(String workspaceId, String repoIdOrSlug, String targetUrl, List<String> events) throws IOException {
        // Try to list existing webhooks first, but handle permission errors gracefully
        try {
            List<VcsWebhook> existingWebhooks = listWebhooks(workspaceId, repoIdOrSlug);
            for (VcsWebhook webhook : existingWebhooks) {
                if (webhook.matchesUrl(targetUrl)) {
                    return updateWebhook(workspaceId, repoIdOrSlug, webhook.id(), targetUrl, events);
                }
            }
        } catch (IOException e) {
            // If listing fails (e.g., 403 Forbidden with repository tokens), 
            // proceed to create a new webhook directly
            log.warn("Could not list webhooks (token may lack read permission), attempting direct creation: {}", e.getMessage());
        }
        
        return createWebhook(workspaceId, repoIdOrSlug, targetUrl, events);
    }
    
    private String createWebhook(String workspaceId, String repoIdOrSlug, String targetUrl, List<String> events) throws IOException {
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = baseUrl + "/projects/" + encodedPath + "/hooks";
        
        log.info("createWebhook: projectPath={}, encodedPath={}, url={}", projectPath, encodedPath, url);
        
        StringBuilder body = new StringBuilder();
        body.append("{\"url\":\"").append(targetUrl).append("\"");
        
        // Convert generic events to GitLab events
        for (String event : events) {
            String gitlabEvent = convertToGitLabEvent(event);
            if (gitlabEvent != null) {
                body.append(",\"").append(gitlabEvent).append("\":true");
            }
        }
        body.append("}");
        
        log.info("createWebhook: body={}", body);

        Request request = createPostRequest(url, body.toString());
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String responseBody = response.body() != null ? response.body().string() : "null";
                log.error("createWebhook failed: code={}, body={}", response.code(), responseBody);
                
                // Provide helpful error messages for common issues
                if (response.code() == 403) {
                    throw new IOException("GitLab webhook creation failed (403 Forbidden). " +
                            "The token must have the Maintainer role to manage webhooks. " +
                            "Please recreate your Project Access Token with Role: Maintainer and Scopes: api, read_repository, write_repository.");
                }
                
                if (response.code() == 422 && responseBody.contains("Invalid url")) {
                    throw new IOException("GitLab webhook creation failed (422 Invalid URL). " +
                            "GitLab requires a publicly accessible webhook URL. " +
                            "The URL '" + targetUrl + "' is not reachable from GitLab. " +
                            "Please configure a public URL in your CodeCrow settings or use a tunnel service like ngrok for local development.");
                }
                
                throw createException("create webhook", response);
            }
            
            JsonNode node = objectMapper.readTree(response.body().string());
            String webhookId = String.valueOf(node.get("id").asLong());
            log.info("createWebhook succeeded: webhookId={}", webhookId);
            return webhookId;
        }
    }
    
    private String updateWebhook(String workspaceId, String repoIdOrSlug, String webhookId, String targetUrl, List<String> events) throws IOException {
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = baseUrl + "/projects/" + encodedPath + "/hooks/" + webhookId;
        
        StringBuilder body = new StringBuilder();
        body.append("{\"url\":\"").append(targetUrl).append("\"");
        
        for (String event : events) {
            String gitlabEvent = convertToGitLabEvent(event);
            if (gitlabEvent != null) {
                body.append(",\"").append(gitlabEvent).append("\":true");
            }
        }
        body.append("}");

        Request request = createPutRequest(url, body.toString());
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("update webhook", response);
            }
            
            return webhookId;
        }
    }
    
    private String convertToGitLabEvent(String event) {
        return switch (event.toLowerCase()) {
            // GitLab native event names (pass through) - support both singular and plural forms
            case "merge_requests_events", "merge_request_events" -> "merge_requests_events";
            case "note_events" -> "note_events";
            case "push_events" -> "push_events";
            // Generic event names (convert to GitLab format)
            case "pullrequest:created", "pullrequest:opened", "pr:opened", 
                 "pullrequest:updated", "pr:updated", "pullrequest:merged", 
                 "pr:merged", "pull_request" -> "merge_requests_events";
            case "pullrequest:comment_created", "pr:comment:added", 
                 "pull_request_review_comment", "issue_comment" -> "note_events";
            case "repo:push", "push" -> "push_events";
            default -> null;
        };
    }
    
    @Override
    public void deleteWebhook(String workspaceId, String repoIdOrSlug, String webhookId) throws IOException {
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = baseUrl + "/projects/" + encodedPath + "/hooks/" + webhookId;

        Request request = createDeleteRequest(url);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful() && response.code() != 404) {
                throw createException("delete webhook", response);
            }
        }
    }
    
    @Override
    public List<VcsWebhook> listWebhooks(String workspaceId, String repoIdOrSlug) throws IOException {
        List<VcsWebhook> webhooks = new ArrayList<>();
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        int page = 1;
        
        log.debug("listWebhooks: projectPath={}, encodedPath={}", projectPath, encodedPath);
        
        while (true) {
            String url = baseUrl + "/projects/" + encodedPath + "/hooks?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
            log.debug("listWebhooks: calling URL={}", url);
            Request request = createGetRequest(url);
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw createException("list webhooks", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                if (!root.isArray() || root.isEmpty()) {
                    break;
                }
                
                for (JsonNode node : root) {
                    webhooks.add(parseWebhook(node));
                }
                
                String nextPage = response.header("X-Next-Page");
                if (nextPage == null || nextPage.isBlank()) {
                    break;
                }
                page++;
            }
        }
        
        return webhooks;
    }
    
    @Override
    public VcsUser getCurrentUser() throws IOException {
        Request request = createGetRequest(baseUrl + "/user");
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("get current user", response);
            }
            
            JsonNode node = objectMapper.readTree(response.body().string());
            return parseUser(node);
        }
    }
    
    @Override
    public VcsWorkspace getWorkspace(String workspaceId) throws IOException {
        // Try as group first
        String encodedWorkspace = URLEncoder.encode(workspaceId, StandardCharsets.UTF_8);
        Request request = createGetRequest(baseUrl + "/groups/" + encodedWorkspace);
        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                JsonNode node = objectMapper.readTree(response.body().string());
                return parseGroup(node);
            }
        }
        
        // Try as user
        request = createGetRequest(baseUrl + "/users?username=" + encodedWorkspace);
        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                JsonNode root = objectMapper.readTree(response.body().string());
                if (root.isArray() && !root.isEmpty()) {
                    JsonNode node = root.get(0);
                    VcsUser user = parseUser(node);
                    return new VcsWorkspace(
                            user.id(),
                            user.username(),
                            user.displayName() != null ? user.displayName() : user.username(),
                            false,
                            user.avatarUrl(),
                            user.htmlUrl()
                    );
                }
            }
            
            if (response.code() == 404) {
                return null;
            }
            throw createException("get workspace/user", response);
        }
    }
    
    @Override
    public byte[] downloadRepositoryArchive(String workspaceId, String repoIdOrSlug, String branchOrCommit) throws IOException {
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = baseUrl + "/projects/" + encodedPath + "/repository/archive.zip?sha=" +
                     URLEncoder.encode(branchOrCommit, StandardCharsets.UTF_8);
        
        Request request = createGetRequest(url);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("download repository archive", response);
            }
            
            ResponseBody body = response.body();
            if (body == null) {
                throw new IOException("Empty response body when downloading archive");
            }
            
            return body.bytes();
        }
    }
    
    @Override
    public long downloadRepositoryArchiveToFile(String workspaceId, String repoIdOrSlug, String branchOrCommit, Path targetFile) throws IOException {
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = baseUrl + "/projects/" + encodedPath + "/repository/archive.zip?sha=" +
                     URLEncoder.encode(branchOrCommit, StandardCharsets.UTF_8);
        
        Request request = createGetRequest(url);
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("download repository archive", response);
            }
            
            ResponseBody body = response.body();
            if (body == null) {
                throw new IOException("Empty response body when downloading archive");
            }
            
            try (InputStream inputStream = body.byteStream();
                 OutputStream outputStream = java.nio.file.Files.newOutputStream(targetFile)) {
                byte[] buffer = new byte[8192];
                long totalBytesRead = 0;
                int bytesRead;
                while ((bytesRead = inputStream.read(buffer)) != -1) {
                    outputStream.write(buffer, 0, bytesRead);
                    totalBytesRead += bytesRead;
                }
                return totalBytesRead;
            }
        }
    }
    
    @Override
    public String getFileContent(String workspaceId, String repoIdOrSlug, String filePath, String branchOrCommit) throws IOException {
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedProjectPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String encodedFilePath = URLEncoder.encode(filePath, StandardCharsets.UTF_8);
        String url = baseUrl + "/projects/" + encodedProjectPath + "/repository/files/" + encodedFilePath + 
                     "/raw?ref=" + URLEncoder.encode(branchOrCommit, StandardCharsets.UTF_8);
        
        Request request = createGetRequest(url);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                if (response.code() == 404) {
                    return null;
                }
                throw createException("get file content", response);
            }
            
            ResponseBody body = response.body();
            if (body == null) {
                return null;
            }
            
            return body.string();
        }
    }
    
    @Override
    public String getLatestCommitHash(String workspaceId, String repoIdOrSlug, String branchName) throws IOException {
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String url = baseUrl + "/projects/" + encodedPath + "/repository/branches/" +
                     URLEncoder.encode(branchName, StandardCharsets.UTF_8);
        
        Request request = createGetRequest(url);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("get latest commit", response);
            }
            
            JsonNode root = objectMapper.readTree(response.body().string());
            JsonNode commit = root.get("commit");
            return commit != null ? getTextOrNull(commit, "id") : null;
        }
    }

    @Override
    public String getBranchDiff(String workspaceId, String repoIdOrSlug, String baseBranch, String compareBranch) throws IOException {
        // GitLab: GET /projects/:id/repository/compare
        // Returns diff between two branches/commits
        // API: https://docs.gitlab.com/ee/api/repositories.html#compare-branches-tags-or-commits
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        String encodedFrom = URLEncoder.encode(baseBranch, StandardCharsets.UTF_8);
        String encodedTo = URLEncoder.encode(compareBranch, StandardCharsets.UTF_8);
        
        String url = baseUrl + "/projects/" + encodedPath + "/repository/compare?from=" + encodedFrom + "&to=" + encodedTo;
        
        Request request = createGetRequest(url);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("get branch diff", response);
            }
            
            JsonNode root = objectMapper.readTree(response.body().string());
            JsonNode diffs = root.get("diffs");
            
            if (diffs == null || !diffs.isArray() || diffs.isEmpty()) {
                return "";
            }
            
            // Build unified diff format from GitLab's compare response
            StringBuilder diffBuilder = new StringBuilder();
            for (JsonNode diff : diffs) {
                String oldPath = getTextOrNull(diff, "old_path");
                String newPath = getTextOrNull(diff, "new_path");
                boolean newFile = diff.has("new_file") && diff.get("new_file").asBoolean();
                boolean deletedFile = diff.has("deleted_file") && diff.get("deleted_file").asBoolean();
                boolean renamedFile = diff.has("renamed_file") && diff.get("renamed_file").asBoolean();
                String diffContent = getTextOrNull(diff, "diff");
                
                // Build git diff header
                diffBuilder.append("diff --git a/").append(oldPath).append(" b/").append(newPath).append("\n");
                
                if (newFile) {
                    diffBuilder.append("new file mode 100644\n");
                } else if (deletedFile) {
                    diffBuilder.append("deleted file mode 100644\n");
                } else if (renamedFile) {
                    diffBuilder.append("rename from ").append(oldPath).append("\n");
                    diffBuilder.append("rename to ").append(newPath).append("\n");
                }
                
                // Proper unified diff headers: /dev/null for new/deleted files
                if (newFile) {
                    diffBuilder.append("--- /dev/null\n");
                    diffBuilder.append("+++ b/").append(newPath).append("\n");
                } else if (deletedFile) {
                    diffBuilder.append("--- a/").append(oldPath).append("\n");
                    diffBuilder.append("+++ /dev/null\n");
                } else {
                    diffBuilder.append("--- a/").append(oldPath).append("\n");
                    diffBuilder.append("+++ b/").append(newPath).append("\n");
                }
                
                if (diffContent != null && !diffContent.isEmpty()) {
                    diffBuilder.append(diffContent);
                    if (!diffContent.endsWith("\n")) {
                        diffBuilder.append("\n");
                    }
                }
            }
            
            return diffBuilder.toString();
        }
    }

    @Override
    public List<String> listBranches(String workspaceId, String repoIdOrSlug) throws IOException {
        List<String> branches = new ArrayList<>();
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        int page = 1;
        
        while (true) {
            String url = baseUrl + "/projects/" + encodedPath + "/repository/branches?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
            Request request = createGetRequest(url);
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw createException("list branches", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                
                if (root == null || !root.isArray() || root.isEmpty()) {
                    break;
                }
                
                for (JsonNode node : root) {
                    String name = getTextOrNull(node, "name");
                    if (name != null) {
                        branches.add(name);
                    }
                }
                
                String nextPage = response.header("X-Next-Page");
                if (nextPage == null || nextPage.isBlank()) {
                    break;
                }
                page++;
            }
        }
        
        return branches;
    }
    
    @Override
    public List<VcsCollaborator> getRepositoryCollaborators(String workspaceId, String repoIdOrSlug) throws IOException {
        List<VcsCollaborator> collaborators = new ArrayList<>();
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        int page = 1;
        
        while (true) {
            String url = baseUrl + "/projects/" + encodedPath + "/members/all?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
            Request request = createGetRequest(url);
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    if (response.code() == 403) {
                        throw new IOException("No permission to view project members.");
                    }
                    throw createException("get project members", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                
                if (root != null && root.isArray()) {
                    for (JsonNode memberNode : root) {
                        VcsCollaborator collab = parseCollaborator(memberNode);
                        if (collab != null) {
                            collaborators.add(collab);
                        }
                    }
                }
                
                String nextPage = response.header("X-Next-Page");
                if (nextPage == null || nextPage.isBlank()) {
                    break;
                }
                page++;
            }
        }
        
        return collaborators;
    }
    
    private VcsCollaborator parseCollaborator(JsonNode node) {
        if (node == null) return null;
        
        String id = String.valueOf(node.get("id").asLong());
        String username = getTextOrNull(node, "username");
        String name = getTextOrNull(node, "name");
        String avatarUrl = getTextOrNull(node, "avatar_url");
        String webUrl = getTextOrNull(node, "web_url");
        
        // GitLab uses access_level numbers
        int accessLevel = node.has("access_level") ? node.get("access_level").asInt() : 0;
        String permission = mapAccessLevel(accessLevel);
        
        return new VcsCollaborator(id, username, name != null ? name : username, avatarUrl, permission, webUrl);
    }
    
    private String mapAccessLevel(int accessLevel) {
        return switch (accessLevel) {
            case 50 -> "owner";
            case 40 -> "maintainer";
            case 30 -> "developer";
            case 20 -> "reporter";
            case 10 -> "guest";
            default -> "unknown";
        };
    }

    private boolean isCurrentUser(String workspaceId) {
        try {
            VcsUser currentUser = getCurrentUser();
            return currentUser != null && currentUser.username().equalsIgnoreCase(workspaceId);
        } catch (IOException e) {
            return false;
        }
    }
    
    private VcsRepositoryPage fetchRepositoryPage(String url, String workspaceId, int page) throws IOException {
        Request request = createGetRequest(url);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("fetch repositories", response);
            }
            
            JsonNode root = objectMapper.readTree(response.body().string());
            
            List<VcsRepository> repos = new ArrayList<>();
            Integer totalCount = null;
            
            // GitLab returns total count in headers
            String totalHeader = response.header("X-Total");
            if (totalHeader != null && !totalHeader.isBlank()) {
                totalCount = Integer.parseInt(totalHeader);
            }
            
            if (root.isArray()) {
                for (JsonNode node : root) {
                    repos.add(parseRepository(node, workspaceId));
                }
            }
            
            String nextPage = response.header("X-Next-Page");
            boolean hasNext = nextPage != null && !nextPage.isBlank();
            boolean hasPrevious = page > 1;
            
            return new VcsRepositoryPage(
                    repos,
                    page,
                    DEFAULT_PAGE_SIZE,
                    repos.size(),
                    totalCount,
                    hasNext,
                    hasPrevious
            );
        }
    }
    
    private VcsRepository parseRepository(JsonNode node, String workspaceIdFallback) {
        String id = String.valueOf(node.get("id").asLong());
        String name = getTextOrNull(node, "name");
        String path = getTextOrNull(node, "path");
        String pathWithNamespace = getTextOrNull(node, "path_with_namespace");
        String description = getTextOrNull(node, "description");
        boolean isPrivate = node.has("visibility") && !"public".equals(node.get("visibility").asText());
        String defaultBranch = getTextOrNull(node, "default_branch");
        String httpUrlToRepo = getTextOrNull(node, "http_url_to_repo");
        String webUrl = getTextOrNull(node, "web_url");
        
        String workspaceSlug = workspaceIdFallback;
        if (node.has("namespace") && node.get("namespace").has("path")) {
            workspaceSlug = node.get("namespace").get("path").asText();
        } else if (pathWithNamespace != null && pathWithNamespace.contains("/")) {
            workspaceSlug = pathWithNamespace.substring(0, pathWithNamespace.indexOf('/'));
        }
        
        String avatarUrl = null;
        if (node.has("avatar_url") && !node.get("avatar_url").isNull()) {
            avatarUrl = node.get("avatar_url").asText();
        }
        
        return new VcsRepository(
                id,
                path != null ? path : name,
                name,
                pathWithNamespace,
                description,
                isPrivate,
                defaultBranch,
                httpUrlToRepo,
                webUrl,
                workspaceSlug,
                avatarUrl
        );
    }
    
    private VcsWorkspace parseGroup(JsonNode node) {
        String id = String.valueOf(node.get("id").asLong());
        String path = getTextOrNull(node, "path");
        String name = getTextOrNull(node, "name");
        if (name == null) {
            name = path;
        }
        
        String avatarUrl = getTextOrNull(node, "avatar_url");
        String webUrl = getTextOrNull(node, "web_url");
        
        return new VcsWorkspace(id, path, name, true, avatarUrl, webUrl);
    }
    
    private VcsUser parseUser(JsonNode node) {
        String id = String.valueOf(node.get("id").asLong());
        String username = getTextOrNull(node, "username");
        String name = getTextOrNull(node, "name");
        String email = getTextOrNull(node, "email");
        String avatarUrl = getTextOrNull(node, "avatar_url");
        String webUrl = getTextOrNull(node, "web_url");
        
        return new VcsUser(id, username, name != null ? name : username, email, avatarUrl, webUrl);
    }
    
    private VcsWebhook parseWebhook(JsonNode node) {
        String id = String.valueOf(node.get("id").asLong());
        String url = getTextOrNull(node, "url");
        boolean active = !node.has("enable_ssl_verification") || node.get("enable_ssl_verification").asBoolean();
        
        List<String> events = new ArrayList<>();
        if (node.has("push_events") && node.get("push_events").asBoolean()) {
            events.add("push");
        }
        if (node.has("merge_requests_events") && node.get("merge_requests_events").asBoolean()) {
            events.add("merge_request");
        }
        if (node.has("note_events") && node.get("note_events").asBoolean()) {
            events.add("note");
        }
        
        return new VcsWebhook(id, url, active, events, null);
    }
    
    private String getTextOrNull(JsonNode node, String field) {
        return node.has(field) && !node.get(field).isNull() ? node.get(field).asText() : null;
    }
    
    private IOException createException(String operation, Response response) throws IOException {
        String body = response.body() != null ? response.body().string() : "";
        GitLabException cause = new GitLabException(operation, response.code(), body);
        return new IOException(cause.getMessage(), cause);
    }

    private Request createPostRequest(String url, String jsonBody) {
        return new Request.Builder()
                .url(url)
                .header(ACCEPT_HEADER, GITLAB_ACCEPT_HEADER)
                .post(RequestBody.create(jsonBody, JSON_MEDIA_TYPE))
                .build();
    }

    private Request createPutRequest(String url, String jsonBody) {
        return new Request.Builder()
                .url(url)
                .header(ACCEPT_HEADER, GITLAB_ACCEPT_HEADER)
                .put(RequestBody.create(jsonBody, JSON_MEDIA_TYPE))
                .build();
    }

    private Request createDeleteRequest(String url) {
        return new Request.Builder()
                .url(url)
                .header(ACCEPT_HEADER, GITLAB_ACCEPT_HEADER)
                .delete()
                .build();
    }

    private Request createGetRequest(String url) {
        return new Request.Builder()
                .url(url)
                .header(ACCEPT_HEADER, GITLAB_ACCEPT_HEADER)
                .get()
                .build();
    }
}
