package org.rostilos.codecrow.vcsclient.bitbucket.cloud;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.model.*;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * VcsClient implementation for Bitbucket Cloud.
 * Supports both manual OAuth connections and Bitbucket App installations.
 */
public class BitbucketCloudClient implements VcsClient {
    
    private static final String API_BASE = "https://api.bitbucket.org/2.0";
    private static final int DEFAULT_PAGE_SIZE = 50;
    private static final MediaType JSON_MEDIA_TYPE = MediaType.parse("application/json");
    
    private final OkHttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final String workspaceSlug; // Optional: default workspace for operations
    
    public BitbucketCloudClient(OkHttpClient httpClient) {
        this(httpClient, null);
    }
    
    public BitbucketCloudClient(OkHttpClient httpClient, String workspaceSlug) {
        this.httpClient = httpClient;
        this.objectMapper = new ObjectMapper();
        this.workspaceSlug = workspaceSlug;
    }
    
    @Override
    public boolean validateConnection() throws IOException {
        Request request = new Request.Builder()
                .url(API_BASE + "/user")
                .header("Accept", "application/json")
                .get()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            return response.isSuccessful();
        }
    }
    
    @Override
    public List<VcsWorkspace> listWorkspaces() throws IOException {
        List<VcsWorkspace> workspaces = new ArrayList<>();
        String url = API_BASE + "/workspaces?pagelen=" + DEFAULT_PAGE_SIZE;
        
        while (url != null) {
            Request request = new Request.Builder()
                    .url(url)
                    .header("Accept", "application/json")
                    .get()
                    .build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw createException("list workspaces", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                JsonNode values = root.get("values");
                
                if (values != null && values.isArray()) {
                    for (JsonNode node : values) {
                        workspaces.add(parseWorkspace(node));
                    }
                }
                
                url = root.has("next") ? root.get("next").asText() : null;
            }
        }
        
        return workspaces;
    }
    
    @Override
    public VcsRepositoryPage listRepositories(String workspaceId, int page) throws IOException {
        String url = API_BASE + "/repositories/" + workspaceId + "?pagelen=" + DEFAULT_PAGE_SIZE;
        if (page > 1) {
            url += "&page=" + page;
        }
        return fetchRepositoryPage(url, workspaceId, page);
    }
    
    @Override
    public VcsRepositoryPage searchRepositories(String workspaceId, String query, int page) throws IOException {
        String encodedQuery = URLEncoder.encode("name~\"" + query + "\"", StandardCharsets.UTF_8);
        String url = API_BASE + "/repositories/" + workspaceId + "?q=" + encodedQuery + "&pagelen=" + DEFAULT_PAGE_SIZE;
        if (page > 1) {
            url += "&page=" + page;
        }
        return fetchRepositoryPage(url, workspaceId, page);
    }
    
    @Override
    public VcsRepository getRepository(String workspaceId, String repoIdOrSlug) throws IOException {
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug;
        
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                if (response.code() == 404) {
                    return null;
                }
                throw createException("get repository", response);
            }
            
            JsonNode node = objectMapper.readTree(response.body().string());
            return parseRepository(node, workspaceId);
        }
    }
    
    @Override
    public String ensureWebhook(String workspaceId, String repoIdOrSlug, String targetUrl, List<String> events) throws IOException {
        // First, check if webhook already exists
        List<VcsWebhook> existingWebhooks = listWebhooks(workspaceId, repoIdOrSlug);
        for (VcsWebhook webhook : existingWebhooks) {
            if (webhook.matchesUrl(targetUrl)) {
                // Webhook already exists, update it
                return updateWebhook(workspaceId, repoIdOrSlug, webhook.id(), targetUrl, events);
            }
        }
        
        // Create new webhook
        return createWebhook(workspaceId, repoIdOrSlug, targetUrl, events);
    }
    
    private String createWebhook(String workspaceId, String repoIdOrSlug, String targetUrl, List<String> events) throws IOException {
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug + "/hooks";
        
        String body = objectMapper.writeValueAsString(new WebhookCreateRequest(
                "CodeCrow Webhook",
                targetUrl,
                true,
                events
        ));
        
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .post(RequestBody.create(body, JSON_MEDIA_TYPE))
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("create webhook", response);
            }
            
            JsonNode node = objectMapper.readTree(response.body().string());
            return node.get("uuid").asText();
        }
    }
    
    private String updateWebhook(String workspaceId, String repoIdOrSlug, String webhookId, String targetUrl, List<String> events) throws IOException {
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug + "/hooks/" + webhookId;
        
        String body = objectMapper.writeValueAsString(new WebhookCreateRequest(
                "CodeCrow Webhook",
                targetUrl,
                true,
                events
        ));
        
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .put(RequestBody.create(body, JSON_MEDIA_TYPE))
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("update webhook", response);
            }
            
            return webhookId;
        }
    }
    
    @Override
    public void deleteWebhook(String workspaceId, String repoIdOrSlug, String webhookId) throws IOException {
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug + "/hooks/" + webhookId;
        
        Request request = new Request.Builder()
                .url(url)
                .delete()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful() && response.code() != 404) {
                throw createException("delete webhook", response);
            }
        }
    }
    
    @Override
    public List<VcsWebhook> listWebhooks(String workspaceId, String repoIdOrSlug) throws IOException {
        List<VcsWebhook> webhooks = new ArrayList<>();
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug + "/hooks?pagelen=" + DEFAULT_PAGE_SIZE;
        
        while (url != null) {
            Request request = new Request.Builder()
                    .url(url)
                    .header("Accept", "application/json")
                    .get()
                    .build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw createException("list webhooks", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                JsonNode values = root.get("values");
                
                if (values != null && values.isArray()) {
                    for (JsonNode node : values) {
                        webhooks.add(parseWebhook(node));
                    }
                }
                
                url = root.has("next") ? root.get("next").asText() : null;
            }
        }
        
        return webhooks;
    }
    
    @Override
    public VcsUser getCurrentUser() throws IOException {
        Request request = new Request.Builder()
                .url(API_BASE + "/user")
                .header("Accept", "application/json")
                .get()
                .build();
        
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
        Request request = new Request.Builder()
                .url(API_BASE + "/workspaces/" + workspaceId)
                .header("Accept", "application/json")
                .get()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                if (response.code() == 404) {
                    return null;
                }
                throw createException("get workspace", response);
            }
            
            JsonNode node = objectMapper.readTree(response.body().string());
            return parseWorkspace(node);
        }
    }
    
    @Override
    public void setRepoVariable(String workspaceId, String repoIdOrSlug, String key, String value, boolean isSecret) throws IOException {
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug + "/pipelines_config/variables/";
        
        String body = objectMapper.writeValueAsString(new RepoVariableRequest(key, value, isSecret));
        
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .post(RequestBody.create(body, JSON_MEDIA_TYPE))
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful() && response.code() != 409) { // 409 = already exists
                throw createException("set repo variable", response);
            }
        }
    }
    
    // ========== Helper Methods ==========
    
    private VcsRepositoryPage fetchRepositoryPage(String url, String workspaceId, int page) throws IOException {
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("fetch repositories", response);
            }
            
            JsonNode root = objectMapper.readTree(response.body().string());
            JsonNode values = root.get("values");
            
            List<VcsRepository> repos = new ArrayList<>();
            if (values != null && values.isArray()) {
                for (JsonNode node : values) {
                    repos.add(parseRepository(node, workspaceId));
                }
            }
            
            Integer totalCount = root.has("size") ? root.get("size").asInt() : null;
            boolean hasNext = root.has("next");
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
        String uuid = node.has("uuid") ? normalizeUuid(node.get("uuid").asText()) : null;
        String slug = getTextOrNull(node, "slug");
        String name = getTextOrNull(node, "name");
        String fullName = getTextOrNull(node, "full_name");
        String description = getTextOrNull(node, "description");
        boolean isPrivate = node.has("is_private") && node.get("is_private").asBoolean();
        
        // Extract workspace slug from the response, with fallback to passed parameter
        String workspaceSlug = workspaceIdFallback;
        
        // First try to get from workspace.slug in the response
        if (node.has("workspace") && node.get("workspace").has("slug")) {
            workspaceSlug = node.get("workspace").get("slug").asText();
        }
        // Fallback: extract from full_name (format: "workspace/repo")
        else if (fullName != null && fullName.contains("/")) {
            workspaceSlug = fullName.substring(0, fullName.indexOf('/'));
        }
        
        String defaultBranch = null;
        if (node.has("mainbranch") && node.get("mainbranch").has("name")) {
            defaultBranch = node.get("mainbranch").get("name").asText();
        }
        
        String cloneUrl = null;
        String htmlUrl = null;
        if (node.has("links")) {
            JsonNode links = node.get("links");
            if (links.has("html") && links.get("html").has("href")) {
                htmlUrl = links.get("html").get("href").asText();
            }
            if (links.has("clone") && links.get("clone").isArray()) {
                for (JsonNode clone : links.get("clone")) {
                    if ("https".equals(getTextOrNull(clone, "name"))) {
                        cloneUrl = getTextOrNull(clone, "href");
                        break;
                    }
                }
            }
        }
        
        String avatarUrl = null;
        if (node.has("links") && node.get("links").has("avatar")) {
            avatarUrl = getTextOrNull(node.get("links").get("avatar"), "href");
        }
        
        return new VcsRepository(
                uuid,
                slug,
                name,
                fullName,
                description,
                isPrivate,
                defaultBranch,
                cloneUrl,
                htmlUrl,
                workspaceSlug,
                avatarUrl
        );
    }
    
    private VcsWorkspace parseWorkspace(JsonNode node) {
        String uuid = node.has("uuid") ? normalizeUuid(node.get("uuid").asText()) : null;
        String slug = getTextOrNull(node, "slug");
        String name = getTextOrNull(node, "name");
        
        String avatarUrl = null;
        String htmlUrl = null;
        if (node.has("links")) {
            JsonNode links = node.get("links");
            if (links.has("avatar") && links.get("avatar").has("href")) {
                avatarUrl = links.get("avatar").get("href").asText();
            }
            if (links.has("html") && links.get("html").has("href")) {
                htmlUrl = links.get("html").get("href").asText();
            }
        }
        
        return new VcsWorkspace(uuid, slug, name, false, avatarUrl, htmlUrl);
    }
    
    private VcsUser parseUser(JsonNode node) {
        String uuid = node.has("uuid") ? normalizeUuid(node.get("uuid").asText()) : null;
        String username = getTextOrNull(node, "username");
        String displayName = getTextOrNull(node, "display_name");
        
        String avatarUrl = null;
        String htmlUrl = null;
        if (node.has("links")) {
            JsonNode links = node.get("links");
            if (links.has("avatar") && links.get("avatar").has("href")) {
                avatarUrl = links.get("avatar").get("href").asText();
            }
            if (links.has("html") && links.get("html").has("href")) {
                htmlUrl = links.get("html").get("href").asText();
            }
        }
        
        return new VcsUser(uuid, username, displayName, null, avatarUrl, htmlUrl);
    }
    
    private VcsWebhook parseWebhook(JsonNode node) {
        String uuid = node.has("uuid") ? normalizeUuid(node.get("uuid").asText()) : null;
        String url = getTextOrNull(node, "url");
        boolean active = node.has("active") && node.get("active").asBoolean();
        String description = getTextOrNull(node, "description");
        
        List<String> events = new ArrayList<>();
        if (node.has("events") && node.get("events").isArray()) {
            for (JsonNode event : node.get("events")) {
                events.add(event.asText());
            }
        }
        
        return new VcsWebhook(uuid, url, active, events, description);
    }
    
    private String getTextOrNull(JsonNode node, String field) {
        return node.has(field) && !node.get(field).isNull() ? node.get(field).asText() : null;
    }
    
    /**
     * Normalize Bitbucket UUID by removing braces.
     */
    private String normalizeUuid(String uuid) {
        if (uuid == null) return null;
        return uuid.replace("{", "").replace("}", "");
    }
    
    private IOException createException(String operation, Response response) throws IOException {
        String body = response.body() != null ? response.body().string() : "";
        return new IOException("Failed to " + operation + ": " + response.code() + " - " + body);
    }
    
    // ========== Request DTOs ==========
    
    private record WebhookCreateRequest(
            String description,
            String url,
            boolean active,
            List<String> events
    ) {}
    
    private record RepoVariableRequest(
            String key,
            String value,
            boolean secured
    ) {}

    // ========== Archive & File Operations ==========

    @Override
    public byte[] downloadRepositoryArchive(String workspaceId, String repoIdOrSlug, String branchOrCommit) throws IOException {
        // Bitbucket Cloud does not have an API endpoint for downloading archives.
        // Instead, we use the web interface URL which supports authenticated downloads:
        // https://bitbucket.org/{workspace}/{repo_slug}/get/{branch_or_commit}.zip
        // The httpClient already has authentication headers configured.
        String url = "https://bitbucket.org/" + workspaceId + "/" + repoIdOrSlug + 
                     "/get/" + URLEncoder.encode(branchOrCommit, StandardCharsets.UTF_8) + ".zip";
        
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/zip")
                .get()
                .build();
        
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
    public long downloadRepositoryArchiveToFile(String workspaceId, String repoIdOrSlug, String branchOrCommit, java.nio.file.Path targetFile) throws IOException {
        // Bitbucket Cloud does not have an API endpoint for downloading archives.
        // Instead, we use the web interface URL which supports authenticated downloads:
        // https://bitbucket.org/{workspace}/{repo_slug}/get/{branch_or_commit}.zip
        String url = "https://bitbucket.org/" + workspaceId + "/" + repoIdOrSlug + 
                     "/get/" + URLEncoder.encode(branchOrCommit, StandardCharsets.UTF_8) + ".zip";
        
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/zip")
                .get()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("download repository archive", response);
            }
            
            ResponseBody body = response.body();
            if (body == null) {
                throw new IOException("Empty response body when downloading archive");
            }
            
            // Stream directly to file to avoid loading entire archive into memory
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
        // Bitbucket Cloud raw file endpoint:
        // GET /repositories/{workspace}/{repo_slug}/src/{commit}/{path}
        String encodedPath = URLEncoder.encode(filePath, StandardCharsets.UTF_8).replace("%2F", "/");
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug + "/src/" + branchOrCommit + "/" + encodedPath;
        
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "*/*")
                .get()
                .build();
        
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
        // Get branch info to get the latest commit
        // GET /repositories/{workspace}/{repo_slug}/refs/branches/{name}
        String encodedBranch = URLEncoder.encode(branchName, StandardCharsets.UTF_8);
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug + "/refs/branches/" + encodedBranch;
        
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("get branch info", response);
            }
            
            JsonNode root = objectMapper.readTree(response.body().string());
            JsonNode target = root.get("target");
            if (target != null && target.has("hash")) {
                return target.get("hash").asText();
            }
            
            return null;
        }
    }

    @Override
    public List<String> listBranches(String workspaceId, String repoIdOrSlug) throws IOException {
        List<String> branches = new ArrayList<>();
        // GET /repositories/{workspace}/{repo_slug}/refs/branches
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug + "/refs/branches?pagelen=" + DEFAULT_PAGE_SIZE;
        
        while (url != null) {
            Request request = new Request.Builder()
                    .url(url)
                    .header("Accept", "application/json")
                    .get()
                    .build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw createException("list branches", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                JsonNode values = root.get("values");
                
                if (values != null && values.isArray()) {
                    for (JsonNode node : values) {
                        String name = node.has("name") ? node.get("name").asText() : null;
                        if (name != null) {
                            branches.add(name);
                        }
                    }
                }
                
                url = root.has("next") ? root.get("next").asText() : null;
            }
        }
        
        return branches;
    }
    
    @Override
    public String getBranchDiff(String workspaceId, String repoIdOrSlug, String baseBranch, String compareBranch) throws IOException {
        // Bitbucket Cloud: GET /repositories/{workspace}/{repo_slug}/diff/{spec}
        // spec format: baseBranch..compareBranch or baseBranch...compareBranch (three dots for merge-base)
        // Using two dots for direct diff between branches
        String spec = URLEncoder.encode(baseBranch + ".." + compareBranch, StandardCharsets.UTF_8);
        String url = API_BASE + "/repositories/" + workspaceId + "/" + repoIdOrSlug + "/diff/" + spec;
        
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "text/plain")
                .get()
                .build();
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("get branch diff", response);
            }
            
            ResponseBody body = response.body();
            return body != null ? body.string() : "";
        }
    }

    @Override
    public List<VcsCollaborator> getRepositoryCollaborators(String workspaceId, String repoIdOrSlug) throws IOException {
        List<VcsCollaborator> collaborators = new ArrayList<>();
        
        // Use the workspace permissions API for repository-level permissions
        // GET /workspaces/{workspace}/permissions/repositories/{repo_slug}
        String url = API_BASE + "/workspaces/" + workspaceId + "/permissions/repositories/" + repoIdOrSlug + "?pagelen=" + DEFAULT_PAGE_SIZE;
        
        while (url != null) {
            Request request = new Request.Builder()
                    .url(url)
                    .header("Accept", "application/json")
                    .get()
                    .build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    // 403 might mean we don't have permission to view collaborators
                    if (response.code() == 403) {
                        throw new IOException("No permission to view repository collaborators. " +
                            "Ensure the connection has 'workspace:read' and 'repository:read' scopes.");
                    }
                    throw createException("get repository collaborators", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                JsonNode values = root.get("values");
                
                if (values != null && values.isArray()) {
                    for (JsonNode permNode : values) {
                        VcsCollaborator collab = parseCollaboratorPermission(permNode);
                        if (collab != null) {
                            collaborators.add(collab);
                        }
                    }
                }
                
                url = root.has("next") && !root.get("next").isNull() ? root.get("next").asText() : null;
            }
        }
        
        return collaborators;
    }
    
    /**
     * Parse a collaborator from Bitbucket's permission response.
     * Response format:
     * {
     *   "permission": "read|write|admin",
     *   "user": {
     *     "uuid": "{...}",
     *     "username": "...",
     *     "display_name": "...",
     *     "links": { "avatar": { "href": "..." }, "html": { "href": "..." } }
     *   }
     * }
     */
    private VcsCollaborator parseCollaboratorPermission(JsonNode permNode) {
        if (permNode == null) return null;
        
        String permission = getTextOrNull(permNode, "permission");
        JsonNode userNode = permNode.get("user");
        
        if (userNode == null) {
            // Could be a group permission, skip for now
            return null;
        }
        
        String uuid = userNode.has("uuid") ? normalizeUuid(userNode.get("uuid").asText()) : null;
        String username = getTextOrNull(userNode, "username");
        String displayName = getTextOrNull(userNode, "display_name");
        
        // Account ID is the preferred unique identifier for Bitbucket users
        String accountId = getTextOrNull(userNode, "account_id");
        String userId = accountId != null ? accountId : uuid;
        
        String avatarUrl = null;
        String htmlUrl = null;
        if (userNode.has("links")) {
            JsonNode links = userNode.get("links");
            if (links.has("avatar") && links.get("avatar").has("href")) {
                avatarUrl = links.get("avatar").get("href").asText();
            }
            if (links.has("html") && links.get("html").has("href")) {
                htmlUrl = links.get("html").get("href").asText();
            }
        }
        
        return new VcsCollaborator(userId, username, displayName, avatarUrl, permission, htmlUrl);
    }
}
