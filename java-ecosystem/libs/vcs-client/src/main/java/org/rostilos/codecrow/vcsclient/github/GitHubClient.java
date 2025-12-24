package org.rostilos.codecrow.vcsclient.github;

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
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * VcsClient implementation for GitHub.
 * Supports both OAuth token-based connections and GitHub App installations.
 */
public class GitHubClient implements VcsClient {
    
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
        try {
            String installationUrl = API_BASE + "/installation/repositories?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
            VcsRepositoryPage installationPage = fetchRepositoryPage(installationUrl, workspaceId, page);
            if (installationPage != null && !installationPage.items().isEmpty()) {
                return installationPage;
            }
        } catch (IOException e) {
            // Not an installation token, try user/org endpoints
        }
        
        // Determine if workspaceId is a user or org
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
