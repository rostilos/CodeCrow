package org.rostilos.codecrow.vcsclient.github;

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
 * VcsClient implementation for GitHub.
 * Supports both OAuth token-based connections and GitHub App installations.
 */
public class GitHubClient implements VcsClient {
    
    private static final Logger log = LoggerFactory.getLogger(GitHubClient.class);
    
    private static final String API_BASE = GitHubConfig.API_BASE;
    private static final int DEFAULT_PAGE_SIZE = GitHubConfig.DEFAULT_PAGE_SIZE;
    private static final MediaType JSON_MEDIA_TYPE = MediaType.parse("application/json");

    private static final String ACCEPT_HEADER = "Accept";
    private static final String GITHUB_API_VERSION_HEADER = "X-GitHub-Api-Version";
    private static final String GITHUB_ACCEPT_HEADER = "application/vnd.github+json";
    private static final String GITHUB_API_VERSION = "2022-11-28";

    private final OkHttpClient httpClient;
    private final ObjectMapper objectMapper;
    
    public GitHubClient(OkHttpClient httpClient) {
        this.httpClient = httpClient;
        this.objectMapper = new ObjectMapper();
    }
    
    @Override
    public boolean validateConnection() throws IOException {
        Request request = createGetRequest(API_BASE + "/user");
        try (Response response = httpClient.newCall(request).execute()) {
            return response.isSuccessful();
        }
    }
    
    @Override
    public List<VcsWorkspace> listWorkspaces() throws IOException {
        List<VcsWorkspace> workspaces = new ArrayList<>();
        
        // GitHub uses organizations instead of workspaces
        VcsUser currentUser = getCurrentUser();
        if (currentUser != null) {
            // Add user's personal namespace as a "workspace"
            workspaces.add(new VcsWorkspace(
                    currentUser.id(),
                    currentUser.username(),
                    currentUser.displayName() != null ? currentUser.displayName() : currentUser.username(),
                    false,
                    currentUser.avatarUrl(),
                    currentUser.htmlUrl()
            ));
        }
        
        int page = 1;
        while (true) {
            String url = API_BASE + "/user/orgs?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
            
            Request request = new Request.Builder()
                    .url(url)
                    .header(ACCEPT_HEADER, GITHUB_ACCEPT_HEADER)
                    .header(GITHUB_API_VERSION_HEADER, GITHUB_API_VERSION)
                    .get()
                    .build();
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw createException("list organizations", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                if (!root.isArray() || root.isEmpty()) {
                    break;
                }
                
                for (JsonNode node : root) {
                    workspaces.add(parseOrganization(node));
                }
                
                // Check for pagination via Link header
                String linkHeader = response.header("Link");
                if (linkHeader == null || !linkHeader.contains("rel=\"next\"")) {
                    break;
                }
                page++;
            }
        }
        
        return workspaces;
    }
    
    @Override
    public VcsRepositoryPage listRepositories(String workspaceId, int page) throws IOException {
        // First, try the installation repositories endpoint.
        // This endpoint only works with GitHub App installation tokens and returns
        // ONLY the repositories that were selected during app installation.
        String installationUrl = API_BASE + "/installation/repositories?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
        log.debug("Trying installation repositories endpoint: {}", installationUrl);
        
        Request installationRequest = createGetRequest(installationUrl);
        try (Response response = httpClient.newCall(installationRequest).execute()) {
            log.debug("Installation repositories response code: {}", response.code());
            
            if (response.isSuccessful()) {
                // Parse installation repositories response
                String responseBody = response.body().string();
                JsonNode root = objectMapper.readTree(responseBody);
                List<VcsRepository> repos = new ArrayList<>();
                Integer totalCount = null;
                
                if (root.has("repositories")) {
                    JsonNode reposArray = root.get("repositories");
                    totalCount = root.has("total_count") ? root.get("total_count").asInt() : null;
                    log.info("GitHub App installation returned {} repositories (total_count={})", 
                            reposArray != null ? reposArray.size() : 0, totalCount);
                    
                    if (reposArray != null && reposArray.isArray()) {
                        for (JsonNode node : reposArray) {
                            repos.add(parseRepository(node, workspaceId));
                        }
                    }
                }
                
                String linkHeader = response.header("Link");
                boolean hasNext = linkHeader != null && linkHeader.contains("rel=\"next\"");
                boolean hasPrevious = page > 1;
                
                // Return installation repos - even if empty, this respects the user's selection
                log.info("Using GitHub App installation repositories: {} repos returned for page {}", repos.size(), page);
                return new VcsRepositoryPage(
                        repos,
                        page,
                        DEFAULT_PAGE_SIZE,
                        repos.size(),
                        totalCount,
                        hasNext,
                        hasPrevious
                );
            } else {
                String errorBody = response.body() != null ? response.body().string() : "";
                log.debug("Installation endpoint returned {}: {}", response.code(), errorBody);
            }
            // If not successful (403/401), fall through to user/org endpoints
        } catch (IOException e) {
            log.debug("Installation endpoint failed with IOException: {}", e.getMessage());
            // Network error or other issue - fall through to user/org endpoints
        }
        
        // Not an installation token, use user/org endpoints which list ALL accessible repos
        log.info("Falling back to user/org repositories endpoint (not using GitHub App installation)");
        String url;
        String sortParams = "&sort=updated&direction=desc";
        if (isCurrentUser(workspaceId)) {
            url = API_BASE + "/user/repos?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page + sortParams;
        } else {
            try {
                url = API_BASE + "/orgs/" + workspaceId + "/repos?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page + sortParams;
                return fetchRepositoryPage(url, workspaceId, page);
            } catch (IOException e) {
                if (e.getMessage() != null && e.getMessage().contains("404")) {
                    url = API_BASE + "/users/" + workspaceId + "/repos?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page + sortParams;
                } else {
                    throw e;
                }
            }
        }
        
        return fetchRepositoryPage(url, workspaceId, page);
    }
    
    @Override
    public VcsRepositoryPage searchRepositories(String workspaceId, String query, int page) throws IOException {
        String encodedQuery = URLEncoder.encode(query, StandardCharsets.UTF_8);
        String ownerFilter = isCurrentUser(workspaceId) ? "user:" + workspaceId : "org:" + workspaceId;

        String url = API_BASE + "/search/repositories?q=" + encodedQuery + "+" + ownerFilter +
                     "&per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
        Request request = createGetRequest(url);
        
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("search repositories", response);
            }
            
            JsonNode root = objectMapper.readTree(response.body().string());
            JsonNode items = root.get("items");
            Integer totalCount = root.has("total_count") ? root.get("total_count").asInt() : null;
            
            List<VcsRepository> repos = new ArrayList<>();
            if (items != null && items.isArray()) {
                for (JsonNode node : items) {
                    repos.add(parseRepository(node, workspaceId));
                }
            }
            
            boolean hasNext = totalCount != null && (page * DEFAULT_PAGE_SIZE) < totalCount;
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
    
    @Override
    public VcsRepository getRepository(String workspaceId, String repoIdOrSlug) throws IOException {
        String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug;

        Request request = createGetRequest(url);
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
        List<VcsWebhook> existingWebhooks = listWebhooks(workspaceId, repoIdOrSlug);
        for (VcsWebhook webhook : existingWebhooks) {
            if (webhook.matchesUrl(targetUrl)) {
                return updateWebhook(workspaceId, repoIdOrSlug, webhook.id(), targetUrl, events);
            }
        }
        
        return createWebhook(workspaceId, repoIdOrSlug, targetUrl, events);
    }
    
    private String createWebhook(String workspaceId, String repoIdOrSlug, String targetUrl, List<String> events) throws IOException {
        String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug + "/hooks";
        
        List<String> githubEvents = convertToGitHubEvents(events);
        
        String body = objectMapper.writeValueAsString(new GitHubWebhookRequest(
                "web",
                new GitHubWebhookConfig(targetUrl, "json", "0"),
                githubEvents,
                true
        ));

        Request request = createPostRequest(url, body);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("create webhook", response);
            }
            
            JsonNode node = objectMapper.readTree(response.body().string());
            return String.valueOf(node.get("id").asLong());
        }
    }
    
    private String updateWebhook(String workspaceId, String repoIdOrSlug, String webhookId, String targetUrl, List<String> events) throws IOException {
        String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug + "/hooks/" + webhookId;
        
        List<String> githubEvents = convertToGitHubEvents(events);
        
        String body = objectMapper.writeValueAsString(new GitHubWebhookRequest(
                "web",
                new GitHubWebhookConfig(targetUrl, "json", "0"),
                githubEvents,
                true
        ));

        Request request = createPatchRequest(url, body);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("update webhook", response);
            }
            
            return webhookId;
        }
    }
    
    @Override
    public void deleteWebhook(String workspaceId, String repoIdOrSlug, String webhookId) throws IOException {
        String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug + "/hooks/" + webhookId;

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
        int page = 1;
        
        while (true) {
            String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug + "/hooks?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
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
                
                String linkHeader = response.header("Link");
                if (linkHeader == null || !linkHeader.contains("rel=\"next\"")) {
                    break;
                }
                page++;
            }
        }
        
        return webhooks;
    }
    
    @Override
    public VcsUser getCurrentUser() throws IOException {
        Request request = createGetRequest(API_BASE + "/user");
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
        Request request = createGetRequest(API_BASE + "/orgs/" + workspaceId);
        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                JsonNode node = objectMapper.readTree(response.body().string());
                return parseOrganization(node);
            }
        }
        
        request = createGetRequest(API_BASE + "/users/" + workspaceId);
        try (Response response = httpClient.newCall(request).execute()) {
            if (response.isSuccessful()) {
                JsonNode node = objectMapper.readTree(response.body().string());
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
            
            if (response.code() == 404) {
                return null;
            }
            throw createException("get workspace/user", response);
        }
    }
    
    @Override
    public byte[] downloadRepositoryArchive(String workspaceId, String repoIdOrSlug, String branchOrCommit) throws IOException {
        String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug + "/zipball/" +
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
        String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug + "/zipball/" + 
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
        String encodedPath = URLEncoder.encode(filePath, StandardCharsets.UTF_8).replace("%2F", "/");
        String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug + "/contents/" + encodedPath + 
                     "?ref=" + URLEncoder.encode(branchOrCommit, StandardCharsets.UTF_8);
        
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
        String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug + "/commits/" +
                     URLEncoder.encode(branchName, StandardCharsets.UTF_8);
        
        Request request = createGetRequest(url);
        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                throw createException("get latest commit", response);
            }
            
            JsonNode root = objectMapper.readTree(response.body().string());
            return getTextOrNull(root, "sha");
        }
    }

    @Override
    public List<String> listBranches(String workspaceId, String repoIdOrSlug) throws IOException {
        List<String> branches = new ArrayList<>();
        
        // GitHub branches API only works with owner/repo format, not numeric IDs
        // If repoIdOrSlug is numeric, we need to resolve it first
        String repoFullName;
        if (repoIdOrSlug.matches("\\d+")) {
            // Numeric ID - fetch repo first to get full_name
            String repoUrl = API_BASE + "/repositories/" + repoIdOrSlug;
            Request repoRequest = createGetRequest(repoUrl);
            try (Response repoResponse = httpClient.newCall(repoRequest).execute()) {
                if (!repoResponse.isSuccessful()) {
                    throw createException("get repository by ID", repoResponse);
                }
                JsonNode repoNode = objectMapper.readTree(repoResponse.body().string());
                repoFullName = getTextOrNull(repoNode, "full_name");
                if (repoFullName == null) {
                    throw new IOException("Repository full_name not found for ID: " + repoIdOrSlug);
                }
            }
        } else {
            // Already in owner/repo format
            repoFullName = workspaceId + "/" + repoIdOrSlug;
        }
        
        String url = API_BASE + "/repos/" + repoFullName + "/branches?per_page=" + DEFAULT_PAGE_SIZE;
        
        while (url != null) {
            Request request = createGetRequest(url);
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw createException("list branches", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                
                if (root != null && root.isArray()) {
                    for (JsonNode node : root) {
                        String name = getTextOrNull(node, "name");
                        if (name != null) {
                            branches.add(name);
                        }
                    }
                }
                
                url = getNextPageUrl(response);
            }
        }
        
        return branches;
    }
    
    @Override
    public String getBranchDiff(String workspaceId, String repoIdOrSlug, String baseBranch, String compareBranch) throws IOException {
        // GitHub: GET /repos/{owner}/{repo}/compare/{basehead}
        // basehead format: base...head (three dots)
        // Returns compare results including the diff
        
        // GitHub API needs owner/repo format
        String repoFullName;
        if (repoIdOrSlug.matches("\\d+")) {
            // Numeric ID - fetch repo first to get full_name
            String repoUrl = API_BASE + "/repositories/" + repoIdOrSlug;
            Request repoRequest = createGetRequest(repoUrl);
            try (Response repoResponse = httpClient.newCall(repoRequest).execute()) {
                if (!repoResponse.isSuccessful()) {
                    throw createException("get repository by ID", repoResponse);
                }
                JsonNode repoNode = objectMapper.readTree(repoResponse.body().string());
                repoFullName = getTextOrNull(repoNode, "full_name");
                if (repoFullName == null) {
                    throw new IOException("Repository full_name not found for ID: " + repoIdOrSlug);
                }
            }
        } else {
            repoFullName = workspaceId + "/" + repoIdOrSlug;
        }
        
        // URL encode the branch names in case they contain special characters
        String basehead = URLEncoder.encode(baseBranch, StandardCharsets.UTF_8) + "..." + 
                         URLEncoder.encode(compareBranch, StandardCharsets.UTF_8);
        String url = API_BASE + "/repos/" + repoFullName + "/compare/" + basehead;
        
        // Request the diff format by using Accept header
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github.v3.diff")
                .header(GITHUB_API_VERSION_HEADER, GITHUB_API_VERSION)
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
    
    /**
     * Get repository collaborators with their permission levels.
     * Uses the GitHub Collaborators API to fetch users with access to the repository.
     * 
     * API: GET /repos/{owner}/{repo}/collaborators
     * 
     * Note: Requires push access to the repository to list collaborators.
     * For organization repos, may require organization admin permissions.
     * 
     * @param workspaceId the owner (user or organization)
     * @param repoIdOrSlug the repository name
     * @return list of collaborators with permissions
     */
    @Override
    public List<VcsCollaborator> getRepositoryCollaborators(String workspaceId, String repoIdOrSlug) throws IOException {
        List<VcsCollaborator> collaborators = new ArrayList<>();
        
        // GitHub API: GET /repos/{owner}/{repo}/collaborators
        // Requires "affiliation=all" to get all collaborators (direct, outside, and from teams)
        String url = API_BASE + "/repos/" + workspaceId + "/" + repoIdOrSlug + "/collaborators?per_page=" + DEFAULT_PAGE_SIZE + "&affiliation=all";
        
        while (url != null) {
            Request request = createGetRequest(url);
            
            try (Response response = httpClient.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    // 403 means we don't have permission to view collaborators
                    if (response.code() == 403) {
                        throw new IOException("No permission to view repository collaborators. " +
                            "Requires push access to the repository.");
                    }
                    throw createException("get repository collaborators", response);
                }
                
                JsonNode root = objectMapper.readTree(response.body().string());
                
                if (root != null && root.isArray()) {
                    for (JsonNode collabNode : root) {
                        VcsCollaborator collab = parseCollaborator(collabNode);
                        if (collab != null) {
                            collaborators.add(collab);
                        }
                    }
                }
                
                // Check for pagination via Link header
                url = getNextPageUrl(response);
            }
        }
        
        return collaborators;
    }
    
    /**
     * Parse a collaborator from GitHub's collaborator response.
     * Response format:
     * {
     *   "id": 12345,
     *   "login": "username",
     *   "avatar_url": "https://...",
     *   "html_url": "https://github.com/username",
     *   "permissions": {
     *     "admin": false,
     *     "maintain": false,
     *     "push": true,
     *     "triage": true,
     *     "pull": true
     *   },
     *   "role_name": "write"
     * }
     */
    private VcsCollaborator parseCollaborator(JsonNode node) {
        if (node == null) return null;
        
        String id = String.valueOf(node.get("id").asLong());
        String login = getTextOrNull(node, "login");
        String avatarUrl = getTextOrNull(node, "avatar_url");
        String htmlUrl = getTextOrNull(node, "html_url");
        
        // Get permission level - prefer role_name if available, otherwise derive from permissions object
        String permission = getTextOrNull(node, "role_name");
        if (permission == null && node.has("permissions")) {
            permission = derivePermissionFromObject(node.get("permissions"));
        }
        
        // GitHub doesn't have a separate display name, use login
        return new VcsCollaborator(id, login, login, avatarUrl, permission, htmlUrl);
    }
    
    /**
     * Derive permission level from GitHub's permissions object.
     * Priority: admin > maintain > push > triage > pull
     */
    private String derivePermissionFromObject(JsonNode permissions) {
        if (permissions == null) return null;
        
        if (permissions.has("admin") && permissions.get("admin").asBoolean()) {
            return "admin";
        }
        if (permissions.has("maintain") && permissions.get("maintain").asBoolean()) {
            return "maintain";
        }
        if (permissions.has("push") && permissions.get("push").asBoolean()) {
            return "write";
        }
        if (permissions.has("triage") && permissions.get("triage").asBoolean()) {
            return "triage";
        }
        if (permissions.has("pull") && permissions.get("pull").asBoolean()) {
            return "read";
        }
        
        return null;
    }
    
    /**
     * Extract the next page URL from GitHub's Link header.
     * Format: <url>; rel="next", <url>; rel="last"
     */
    private String getNextPageUrl(Response response) {
        String linkHeader = response.header("Link");
        if (linkHeader == null) return null;
        
        // Parse Link header for rel="next"
        for (String link : linkHeader.split(",")) {
            String[] parts = link.split(";");
            if (parts.length >= 2) {
                String rel = parts[1].trim();
                if (rel.equals("rel=\"next\"")) {
                    String url = parts[0].trim();
                    // Remove < and > brackets
                    if (url.startsWith("<") && url.endsWith(">")) {
                        return url.substring(1, url.length() - 1);
                    }
                }
            }
        }
        
        return null;
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
            
            JsonNode reposArray;
            if (root.isArray()) {
                reposArray = root;
            } else if (root.has("repositories")) {
                reposArray = root.get("repositories");
                totalCount = root.has("total_count") ? root.get("total_count").asInt() : null;
            } else {
                reposArray = root;
            }
            
            if (reposArray != null && reposArray.isArray()) {
                for (JsonNode node : reposArray) {
                    repos.add(parseRepository(node, workspaceId));
                }
            }
            
            String linkHeader = response.header("Link");
            boolean hasNext = linkHeader != null && linkHeader.contains("rel=\"next\"");
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
        String fullName = getTextOrNull(node, "full_name");
        String description = getTextOrNull(node, "description");
        boolean isPrivate = node.has("private") && node.get("private").asBoolean();
        String defaultBranch = getTextOrNull(node, "default_branch");
        String cloneUrl = getTextOrNull(node, "clone_url");
        String htmlUrl = getTextOrNull(node, "html_url");
        
        String workspaceSlug = workspaceIdFallback;
        if (node.has("owner") && node.get("owner").has("login")) {
            workspaceSlug = node.get("owner").get("login").asText();
        } else if (fullName != null && fullName.contains("/")) {
            workspaceSlug = fullName.substring(0, fullName.indexOf('/'));
        }
        
        String avatarUrl = null;
        if (node.has("owner") && node.get("owner").has("avatar_url")) {
            avatarUrl = node.get("owner").get("avatar_url").asText();
        }
        
        return new VcsRepository(
                id,
                name,
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
    
    private VcsWorkspace parseOrganization(JsonNode node) {
        String id = String.valueOf(node.get("id").asLong());
        String login = getTextOrNull(node, "login");
        String name = getTextOrNull(node, "name");
        if (name == null) {
            name = login;
        }
        
        String avatarUrl = getTextOrNull(node, "avatar_url");
        String htmlUrl = node.has("url") ? "https://github.com/" + login : null;
        
        return new VcsWorkspace(id, login, name, true, avatarUrl, htmlUrl);
    }
    
    private VcsUser parseUser(JsonNode node) {
        String id = String.valueOf(node.get("id").asLong());
        String login = getTextOrNull(node, "login");
        String name = getTextOrNull(node, "name");
        String email = getTextOrNull(node, "email");
        String avatarUrl = getTextOrNull(node, "avatar_url");
        String htmlUrl = getTextOrNull(node, "html_url");
        
        return new VcsUser(id, login, name != null ? name : login, email, avatarUrl, htmlUrl);
    }
    
    private VcsWebhook parseWebhook(JsonNode node) {
        String id = String.valueOf(node.get("id").asLong());
        String url = null;
        if (node.has("config") && node.get("config").has("url")) {
            url = node.get("config").get("url").asText();
        }
        boolean active = node.has("active") && node.get("active").asBoolean();
        String name = getTextOrNull(node, "name");
        
        List<String> events = new ArrayList<>();
        if (node.has("events") && node.get("events").isArray()) {
            for (JsonNode event : node.get("events")) {
                events.add(event.asText());
            }
        }
        
        return new VcsWebhook(id, url, active, events, name);
    }
    
    private List<String> convertToGitHubEvents(List<String> events) {
        List<String> githubEvents = new ArrayList<>();
        for (String event : events) {
            String githubEvent = switch (event.toLowerCase()) {
                case "pullrequest:created", "pullrequest:opened", "pr:opened" -> "pull_request";
                case "pullrequest:updated", "pr:updated" -> "pull_request";
                case "pullrequest:merged", "pr:merged" -> "pull_request";
                case "pullrequest:comment_created", "pr:comment:added" -> "pull_request_review_comment";
                case "repo:push", "push" -> "push";
                case "issue_comment" -> "issue_comment";
                default -> event;
            };
            if (!githubEvents.contains(githubEvent)) {
                githubEvents.add(githubEvent);
            }
        }
        return githubEvents;
    }
    
    private String getTextOrNull(JsonNode node, String field) {
        return node.has(field) && !node.get(field).isNull() ? node.get(field).asText() : null;
    }
    
    private IOException createException(String operation, Response response) throws IOException {
        String body = response.body() != null ? response.body().string() : "";
        GitHubException cause = new GitHubException(operation, response.code(), body);
        return new IOException(cause.getMessage(), cause);
    }

    private Request createPostRequest(String url, String jsonBody) {
        return new Request.Builder()
                .url(url)
                .header(ACCEPT_HEADER, GITHUB_ACCEPT_HEADER)
                .header(GITHUB_API_VERSION_HEADER, GITHUB_API_VERSION)
                .post(RequestBody.create(jsonBody, JSON_MEDIA_TYPE))
                .build();
    }

    private Request createPatchRequest(String url, String jsonBody) {
        return new Request.Builder()
                .url(url)
                .header(ACCEPT_HEADER, GITHUB_ACCEPT_HEADER)
                .header(GITHUB_API_VERSION_HEADER, GITHUB_API_VERSION)
                .patch(RequestBody.create(jsonBody, JSON_MEDIA_TYPE))
                .build();
    }

    private Request createDeleteRequest(String url) {
        return new Request.Builder()
                .url(url)
                .header(ACCEPT_HEADER, GITHUB_ACCEPT_HEADER)
                .header(GITHUB_API_VERSION_HEADER, GITHUB_API_VERSION)
                .delete()
                .build();
    }

    private Request createGetRequest(String url) {
        return new Request.Builder()
                .url(url)
                .header(ACCEPT_HEADER, GITHUB_ACCEPT_HEADER)
                .header(GITHUB_API_VERSION_HEADER, GITHUB_API_VERSION)
                .get()
                .build();
    }
    

    private record GitHubWebhookRequest(
            String name,
            GitHubWebhookConfig config,
            List<String> events,
            boolean active
    ) {}
    
    private record GitHubWebhookConfig(
            String url,
            String content_type,
            String insecure_ssl
    ) {}
}
