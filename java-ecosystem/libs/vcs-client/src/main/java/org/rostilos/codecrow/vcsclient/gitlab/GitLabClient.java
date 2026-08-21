package org.rostilos.codecrow.vcsclient.gitlab;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.*;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.gitlab.api.GitLabApiContext;
import org.rostilos.codecrow.vcsclient.gitlab.api.GitLabDiffApi;
import org.rostilos.codecrow.vcsclient.gitlab.api.GitLabMergeRequestApi;
import org.rostilos.codecrow.vcsclient.gitlab.api.GitLabRepositoryApi;
import org.rostilos.codecrow.vcsclient.model.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * VcsClient implementation for GitLab.
 * Supports OAuth token-based connections.
 */
public class GitLabClient implements VcsClient {
    
    private static final Logger log = LoggerFactory.getLogger(GitLabClient.class);
    
    private static final int DEFAULT_PAGE_SIZE = GitLabConfig.DEFAULT_PAGE_SIZE;

    private final GitLabApiContext api;
    private final GitLabDiffApi diffApi;
    private final GitLabMergeRequestApi mergeRequestApi;
    private final GitLabRepositoryApi repositoryApi;
    
    public GitLabClient(OkHttpClient httpClient) {
        this(httpClient, GitLabConfig.INSTANCE_BASE);
    }
    
    /**
     * Create a GitLab client for an instance root or REST v4 base URL.
     *
     * <p>Both {@code https://gitlab.example.com} and
     * {@code https://gitlab.example.com/api/v4} are accepted. Normalizing here
     * keeps callers from having to understand GitLab endpoint construction.</p>
     */
    public GitLabClient(OkHttpClient httpClient, String instanceBaseUrl) {
        this.api = new GitLabApiContext(httpClient, instanceBaseUrl);
        this.diffApi = new GitLabDiffApi(api);
        this.mergeRequestApi = new GitLabMergeRequestApi(api);
        this.repositoryApi = new GitLabRepositoryApi(api);
    }
    
    @Override
    public boolean validateConnection() throws IOException {
        Request request = api.get(api.apiBaseUrl() + "/user");
        try (Response response = api.execute(request)) {
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
            String url = api.apiBaseUrl() + "/groups?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page + "&min_access_level=10";
            
            Request request = api.get(url);
            
            try (Response response = api.execute(request)) {
                if (!response.isSuccessful()) {
                    throw api.error("list groups", response);
                }
                
                JsonNode root = api.objectMapper().readTree(response.body().string());
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
            url = api.apiBaseUrl() + "/projects?membership=true&per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page + sortParams;
        } else {
            // Try as group first
            String encodedWorkspace = api.encode(workspaceId);
            url = api.apiBaseUrl() + "/groups/" + encodedWorkspace + "/projects?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page + sortParams;
        }
        
        return fetchRepositoryPage(url, workspaceId, page);
    }
    
    @Override
    public VcsRepositoryPage searchRepositories(String workspaceId, String query, int page) throws IOException {
        String encodedQuery = api.encode(query);
        
        String url;
        if (isCurrentUser(workspaceId)) {
            url = api.apiBaseUrl() + "/projects?search=" + encodedQuery + "&membership=true&per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
        } else {
            String encodedWorkspace = api.encode(workspaceId);
            url = api.apiBaseUrl() + "/groups/" + encodedWorkspace + "/projects?search=" + encodedQuery + "&per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
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
        
        String encodedPath = api.encode(projectPath);
        String url = api.apiBaseUrl() + "/projects/" + encodedPath;

        Request request = api.get(url);
        try (Response response = api.execute(request)) {
            if (!response.isSuccessful()) {
                if (response.code() == 404) {
                    // Try with just the repo ID (might be a numeric ID)
                    url = api.apiBaseUrl() + "/projects/" + api.encode(repoIdOrSlug);
                    Request retryRequest = api.get(url);
                    try (Response retryResponse = api.execute(retryRequest)) {
                        if (!retryResponse.isSuccessful()) {
                            if (retryResponse.code() == 404) {
                                return null;
                            }
                            throw api.error("get repository", retryResponse);
                        }
                        JsonNode node = api.objectMapper().readTree(retryResponse.body().string());
                        return parseRepository(node, effectiveNamespace);
                    }
                }
                throw api.error("get repository", response);
            }
            
            JsonNode node = api.objectMapper().readTree(response.body().string());
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
        String url = api.projectUrl(workspaceId, repoIdOrSlug) + "/hooks";
        
        log.info("createWebhook: projectPath={}, url={}", projectPath, url);
        
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

        Request request = api.postJson(url, body.toString());
        try (Response response = api.execute(request)) {
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
                
                throw api.error("create webhook", response);
            }
            
            JsonNode node = api.objectMapper().readTree(response.body().string());
            String webhookId = String.valueOf(node.get("id").asLong());
            log.info("createWebhook succeeded: webhookId={}", webhookId);
            return webhookId;
        }
    }
    
    private String updateWebhook(String workspaceId, String repoIdOrSlug, String webhookId, String targetUrl, List<String> events) throws IOException {
        String url = api.projectUrl(workspaceId, repoIdOrSlug)
                + "/hooks/" + webhookId;
        
        StringBuilder body = new StringBuilder();
        body.append("{\"url\":\"").append(targetUrl).append("\"");
        
        for (String event : events) {
            String gitlabEvent = convertToGitLabEvent(event);
            if (gitlabEvent != null) {
                body.append(",\"").append(gitlabEvent).append("\":true");
            }
        }
        body.append("}");

        Request request = api.putJson(url, body.toString());
        try (Response response = api.execute(request)) {
            if (!response.isSuccessful()) {
                throw api.error("update webhook", response);
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
        String url = api.projectUrl(workspaceId, repoIdOrSlug)
                + "/hooks/" + webhookId;

        Request request = api.delete(url);
        try (Response response = api.execute(request)) {
            if (!response.isSuccessful() && response.code() != 404) {
                throw api.error("delete webhook", response);
            }
        }
    }
    
    @Override
    public List<VcsWebhook> listWebhooks(String workspaceId, String repoIdOrSlug) throws IOException {
        List<VcsWebhook> webhooks = new ArrayList<>();
        String projectPath = workspaceId + "/" + repoIdOrSlug;
        int page = 1;
        
        log.debug("listWebhooks: projectPath={}", projectPath);
        
        while (true) {
            String url = api.projectUrl(workspaceId, repoIdOrSlug)
                    + "/hooks?per_page=" + DEFAULT_PAGE_SIZE + "&page=" + page;
            log.debug("listWebhooks: calling URL={}", url);
            Request request = api.get(url);
            try (Response response = api.execute(request)) {
                if (!response.isSuccessful()) {
                    throw api.error("list webhooks", response);
                }
                
                JsonNode root = api.objectMapper().readTree(response.body().string());
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
        Request request = api.get(api.apiBaseUrl() + "/user");
        try (Response response = api.execute(request)) {
            if (!response.isSuccessful()) {
                throw api.error("get current user", response);
            }
            
            JsonNode node = api.objectMapper().readTree(response.body().string());
            return parseUser(node);
        }
    }
    
    @Override
    public VcsWorkspace getWorkspace(String workspaceId) throws IOException {
        // Try as group first
        String encodedWorkspace = api.encode(workspaceId);
        Request request = api.get(api.apiBaseUrl() + "/groups/" + encodedWorkspace);
        try (Response response = api.execute(request)) {
            if (response.isSuccessful()) {
                JsonNode node = api.objectMapper().readTree(response.body().string());
                return parseGroup(node);
            }
        }
        
        // Try as user
        request = api.get(api.apiBaseUrl() + "/users?username=" + encodedWorkspace);
        try (Response response = api.execute(request)) {
            if (response.isSuccessful()) {
                JsonNode root = api.objectMapper().readTree(response.body().string());
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
            throw api.error("get workspace/user", response);
        }
    }
    
    @Override
    public byte[] downloadRepositoryArchive(String workspaceId, String repoIdOrSlug, String branchOrCommit) throws IOException {
        String url = api.projectUrl(workspaceId, repoIdOrSlug)
                + "/repository/archive.zip?sha=" + api.encode(branchOrCommit);
        
        Request request = api.get(url);
        try (Response response = api.execute(request)) {
            if (!response.isSuccessful()) {
                throw api.error("download repository archive", response);
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
        String url = api.projectUrl(workspaceId, repoIdOrSlug)
                + "/repository/archive.zip?sha=" + api.encode(branchOrCommit);
        
        Request request = api.get(url);
        
        try (Response response = api.execute(request)) {
            if (!response.isSuccessful()) {
                throw api.error("download repository archive", response);
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
        String url = api.projectUrl(workspaceId, repoIdOrSlug)
                + "/repository/files/" + api.encode(filePath)
                + "/raw?ref=" + api.encode(branchOrCommit);
        
        Request request = api.get(url);
        try (Response response = api.execute(request)) {
            if (!response.isSuccessful()) {
                if (response.code() == 404) {
                    return null;
                }
                throw api.error("get file content", response);
            }
            
            ResponseBody body = response.body();
            if (body == null) {
                return null;
            }
            
            return body.string();
        }
    }

    @Override
    public List<String> listRepositoryFiles(
            String workspaceId,
            String repoIdOrSlug,
            String commit,
            int maxFiles
    ) throws IOException {
        return repositoryApi.listFiles(
                workspaceId, repoIdOrSlug, commit, maxFiles);
    }
    
    @Override
    public String getLatestCommitHash(String workspaceId, String repoIdOrSlug, String branchName) throws IOException {
        String url = api.projectUrl(workspaceId, repoIdOrSlug)
                + "/repository/branches/" + api.encode(branchName);
        
        Request request = api.get(url);
        try (Response response = api.execute(request)) {
            if (!response.isSuccessful()) {
                throw api.error("get latest commit", response);
            }
            
            JsonNode root = api.objectMapper().readTree(response.body().string());
            JsonNode commit = root.get("commit");
            return commit != null ? getTextOrNull(commit, "id") : null;
        }
    }

    @Override
    public List<VcsCommit> getCommitHistory(String workspaceId, String repoIdOrSlug, String branchOrCommit, int limit) throws IOException {
        String projectUrl = api.projectUrl(workspaceId, repoIdOrSlug);
        String encodedRef = api.encode(branchOrCommit);
        int perPage = Math.min(limit, 100); // GitLab max per_page is 100

        String url = projectUrl + "/repository/commits?ref_name="
                + encodedRef + "&per_page=" + perPage;

        List<VcsCommit> commits = new ArrayList<>();

        while (url != null && commits.size() < limit) {
            Request request = api.get(url);
            try (Response response = api.execute(request)) {
                if (!response.isSuccessful()) {
                    throw api.error("get commit history", response);
                }

                JsonNode root = api.objectMapper().readTree(response.body().string());
                if (root == null || !root.isArray()) break;

                for (JsonNode commitNode : root) {
                    if (commits.size() >= limit) break;

                    String hash = getTextOrNull(commitNode, "id");
                    if (hash == null) continue;

                    String message = getTextOrNull(commitNode, "message");
                    String authorName = getTextOrNull(commitNode, "author_name");
                    String authorEmail = getTextOrNull(commitNode, "author_email");
                    OffsetDateTime timestamp = null;

                    String dateStr = getTextOrNull(commitNode, "authored_date");
                    if (dateStr != null) {
                        try {
                            timestamp = OffsetDateTime.parse(dateStr, DateTimeFormatter.ISO_OFFSET_DATE_TIME);
                        } catch (Exception e) {
                            log.debug("Could not parse commit date '{}': {}", dateStr, e.getMessage());
                        }
                    }

                    List<String> parentHashes = new ArrayList<>();
                    JsonNode parentsArray = commitNode.get("parent_ids");
                    if (parentsArray != null && parentsArray.isArray()) {
                        for (JsonNode parentId : parentsArray) {
                            if (!parentId.isNull()) {
                                parentHashes.add(parentId.asText());
                            }
                        }
                    }

                    commits.add(new VcsCommit(hash, message, authorName, authorEmail, timestamp, parentHashes));
                }

                // Follow pagination via Link header or X-Next-Page
                if (commits.size() < limit) {
                    String nextPage = response.header("X-Next-Page");
                    if (nextPage != null && !nextPage.isEmpty()) {
                        url = projectUrl + "/repository/commits?ref_name=" + encodedRef
                                + "&per_page=" + perPage + "&page=" + nextPage;
                    } else {
                        url = null;
                    }
                } else {
                    url = null;
                }
            }
        }

        return commits;
    }

    @Override
    public String getBranchDiff(String workspaceId, String repoIdOrSlug, String baseBranch, String compareBranch) throws IOException {
        return diffApi.getCommitRangeDiff(
                workspaceId, repoIdOrSlug, baseBranch, compareBranch);
    }

    @Override
    public List<String> listBranches(String workspaceId, String repoIdOrSlug) throws IOException {
        List<String> branches = new ArrayList<>();
        int page = 1;
        
        while (true) {
            String url = api.projectUrl(workspaceId, repoIdOrSlug)
                    + "/repository/branches?per_page=" + DEFAULT_PAGE_SIZE
                    + "&page=" + page;
            Request request = api.get(url);
            
            try (Response response = api.execute(request)) {
                if (!response.isSuccessful()) {
                    throw api.error("list branches", response);
                }
                
                JsonNode root = api.objectMapper().readTree(response.body().string());
                
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
        int page = 1;
        
        while (true) {
            String url = api.projectUrl(workspaceId, repoIdOrSlug)
                    + "/members/all?per_page=" + DEFAULT_PAGE_SIZE
                    + "&page=" + page;
            Request request = api.get(url);
            
            try (Response response = api.execute(request)) {
                if (!response.isSuccessful()) {
                    if (response.code() == 403) {
                        throw new IOException("No permission to view project members.");
                    }
                    throw api.error("get project members", response);
                }
                
                JsonNode root = api.objectMapper().readTree(response.body().string());
                
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

    @Override
    public VcsPullRequest getPullRequest(
            String workspaceId,
            String repoIdOrSlug,
            long pullRequestNumber
    ) throws IOException {
        JsonNode metadata = getMergeRequest(workspaceId, repoIdOrSlug, pullRequestNumber);
        String state = getTextOrNull(metadata, "state");
        String baseCommit = metadata.path("diff_refs").path("base_sha").asText(null);
        if (baseCommit == null || baseCommit.isBlank()) {
            baseCommit = metadata.path("diff_refs").path("start_sha").asText(null);
        }
        String headCommit = metadata.path("diff_refs").path("head_sha").asText(null);
        if (headCommit == null || headCommit.isBlank()) {
            headCommit = metadata.path("sha").asText(null);
        }
        return new VcsPullRequest(
                pullRequestNumber,
                getTextOrNull(metadata, "title"),
                getTextOrNull(metadata, "description"),
                getTextOrNull(metadata, "source_branch"),
                getTextOrNull(metadata, "target_branch"),
                baseCommit,
                headCommit,
                state,
                "merged".equalsIgnoreCase(state),
                getTextOrNull(metadata, "web_url"));
    }

    @Override
    public List<VcsPullRequestComment> getPullRequestCommentThread(
            String workspaceId,
            String repoIdOrSlug,
            long pullRequestNumber,
            String triggeringCommentId,
            String parentOrThreadId,
            boolean inlineComment
    ) throws IOException {
        if (parentOrThreadId == null || parentOrThreadId.isBlank()) {
            return List.of();
        }

        JsonNode discussion = mergeRequestApi.getDiscussion(
                workspaceId, repoIdOrSlug, pullRequestNumber, parentOrThreadId);
        JsonNode notes = discussion.path("notes");
        if (!notes.isArray()) {
            return List.of();
        }

        List<VcsPullRequestComment> comments = new ArrayList<>();
        String rootNoteId = notes.isEmpty() ? null : notes.get(0).path("id").asText(null);
        for (JsonNode note : notes) {
            String noteId = note.path("id").asText();
            comments.add(new VcsPullRequestComment(
                    noteId,
                    noteId.equals(rootNoteId) ? null : rootNoteId,
                    parentOrThreadId,
                    note.path("author").path("username").asText(null),
                    note.path("body").asText(null),
                    note.path("created_at").asText(null)));
        }
        return List.copyOf(comments);
    }

    @Override
    public String getPullRequestDiff(
            String workspaceId,
            String repoIdOrSlug,
            long pullRequestNumber
    ) throws IOException {
        return diffApi.getMergeRequestDiff(
                workspaceId, repoIdOrSlug, pullRequestNumber);
    }

    @Override
    public String getCommitDiff(
            String workspaceId,
            String repoIdOrSlug,
            String commitHash
    ) throws IOException {
        return diffApi.getCommitDiff(workspaceId, repoIdOrSlug, commitHash);
    }

    @Override
    public String getCommitRangeDiff(
            String workspaceId,
            String repoIdOrSlug,
            String baseCommitHash,
            String headCommitHash
    ) throws IOException {
        return diffApi.getCommitRangeDiff(
                workspaceId, repoIdOrSlug, baseCommitHash, headCommitHash);
    }

    @Override
    public boolean fileExists(
            String workspaceId,
            String repoIdOrSlug,
            String branchOrCommit,
            String filePath
    ) throws IOException {
        return repositoryApi.fileExists(
                workspaceId, repoIdOrSlug, branchOrCommit, filePath);
    }

    @Override
    public Long findPullRequestForCommit(
            String workspaceId,
            String repoIdOrSlug,
            String commitHash
    ) throws IOException {
        return mergeRequestApi.findForCommit(workspaceId, repoIdOrSlug, commitHash);
    }

    /**
     * Provider-specific metadata for consumers that need GitLab diff refs.
     * Endpoint construction remains owned by this configured client.
     */
    public JsonNode getMergeRequest(
            String workspaceId,
            String repoIdOrSlug,
            long pullRequestNumber
    ) throws IOException {
        return mergeRequestApi.get(workspaceId, repoIdOrSlug, pullRequestNumber);
    }

    public void postMergeRequestComment(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid,
            String body
    ) throws IOException {
        mergeRequestApi.postComment(
                workspaceId, repoIdOrSlug, mergeRequestIid, body);
    }

    public String postMergeRequestDiscussionReply(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid,
            String discussionId,
            String body
    ) throws IOException {
        JsonNode note = mergeRequestApi.postDiscussionReply(
                workspaceId, repoIdOrSlug, mergeRequestIid, discussionId, body);
        return note.hasNonNull("id") ? note.path("id").asText() : null;
    }

    public void postMergeRequestLineComment(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid,
            String body,
            String baseSha,
            String headSha,
            String startSha,
            String filePath,
            int newLine
    ) throws IOException {
        mergeRequestApi.postLineComment(
                workspaceId, repoIdOrSlug, mergeRequestIid,
                body, baseSha, headSha, startSha, filePath, newLine);
    }

    public List<Map<String, Object>> listMergeRequestNotes(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid
    ) throws IOException {
        return mergeRequestApi.listNotes(
                workspaceId, repoIdOrSlug, mergeRequestIid);
    }

    public void updateMergeRequestNote(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid,
            long noteId,
            String body
    ) throws IOException {
        mergeRequestApi.updateNote(
                workspaceId, repoIdOrSlug, mergeRequestIid, noteId, body);
    }

    public void deleteMergeRequestNote(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid,
            long noteId
    ) throws IOException {
        mergeRequestApi.deleteNote(
                workspaceId, repoIdOrSlug, mergeRequestIid, noteId);
    }

    public Long findMergeRequestNoteByMarker(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid,
            String marker
    ) throws IOException {
        return mergeRequestApi.findNoteByMarker(
                workspaceId, repoIdOrSlug, mergeRequestIid, marker);
    }

    public JsonNode listMergeRequests(
            String workspaceId,
            String repoIdOrSlug,
            String state,
            int limit
    ) throws IOException {
        return mergeRequestApi.list(workspaceId, repoIdOrSlug, state, limit);
    }

    public JsonNode createMergeRequest(
            String workspaceId,
            String repoIdOrSlug,
            String title,
            String description,
            String sourceBranch,
            String targetBranch,
            List<String> reviewerIds
    ) throws IOException {
        return mergeRequestApi.create(
                workspaceId,
                repoIdOrSlug,
                title,
                description,
                sourceBranch,
                targetBranch,
                reviewerIds);
    }

    public JsonNode updateMergeRequest(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid,
            String title,
            String description
    ) throws IOException {
        return mergeRequestApi.update(
                workspaceId, repoIdOrSlug, mergeRequestIid, title, description);
    }

    public JsonNode getMergeRequestActivity(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid
    ) throws IOException {
        return mergeRequestApi.getActivity(
                workspaceId, repoIdOrSlug, mergeRequestIid);
    }

    public JsonNode approveMergeRequest(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid
    ) throws IOException {
        return mergeRequestApi.approve(workspaceId, repoIdOrSlug, mergeRequestIid);
    }

    public JsonNode unapproveMergeRequest(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid
    ) throws IOException {
        return mergeRequestApi.unapprove(
                workspaceId, repoIdOrSlug, mergeRequestIid);
    }

    public JsonNode closeMergeRequest(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid
    ) throws IOException {
        return mergeRequestApi.close(workspaceId, repoIdOrSlug, mergeRequestIid);
    }

    public JsonNode mergeMergeRequest(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid,
            String message,
            String strategy
    ) throws IOException {
        return mergeRequestApi.merge(
                workspaceId,
                repoIdOrSlug,
                mergeRequestIid,
                message,
                strategy);
    }

    public JsonNode getMergeRequestNotes(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid
    ) throws IOException {
        return mergeRequestApi.getNotes(
                workspaceId, repoIdOrSlug, mergeRequestIid);
    }

    public JsonNode getMergeRequestCommits(
            String workspaceId,
            String repoIdOrSlug,
            long mergeRequestIid
    ) throws IOException {
        return mergeRequestApi.getCommits(
                workspaceId, repoIdOrSlug, mergeRequestIid);
    }

    public String getRepositoryTree(
            String workspaceId,
            String repoIdOrSlug,
            String branchOrCommit,
            String directoryPath
    ) throws IOException {
        return repositoryApi.getTree(
                workspaceId, repoIdOrSlug, branchOrCommit, directoryPath);
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
        Request request = api.get(url);
        try (Response response = api.execute(request)) {
            if (!response.isSuccessful()) {
                throw api.error("fetch repositories", response);
            }
            
            JsonNode root = api.objectMapper().readTree(response.body().string());
            
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
    
    /**
     * Batch fetch file contents with parallel execution and exponential backoff.
     * GitLab doesn't have a batch API, so we fetch in parallel with rate limit handling.
     */
    @Override
    public java.util.Map<String, String> getFileContents(
            String workspaceId, 
            String repoIdOrSlug, 
            java.util.List<String> filePaths, 
            String branchOrCommit,
            int maxFileSizeBytes
    ) throws IOException {
        java.util.Map<String, String> results = new java.util.concurrent.ConcurrentHashMap<>();
        
        // Use parallel stream with controlled concurrency
        int parallelism = Math.min(10, filePaths.size()); // Max 10 concurrent requests
        java.util.concurrent.ForkJoinPool customPool = new java.util.concurrent.ForkJoinPool(parallelism);
        
        try {
            customPool.submit(() -> 
                filePaths.parallelStream().forEach(path -> {
                    int maxRetries = 3;
                    int retryCount = 0;
                    long backoffMs = 1000; // Start with 1 second
                    
                    while (retryCount < maxRetries) {
                        try {
                            String content = getFileContent(workspaceId, repoIdOrSlug, path, branchOrCommit);
                            if (content != null && content.getBytes(java.nio.charset.StandardCharsets.UTF_8).length <= maxFileSizeBytes) {
                                results.put(path, content);
                            }
                            break; // Success, exit retry loop
                        } catch (IOException e) {
                            retryCount++;
                            if (e.getMessage() != null && (e.getMessage().contains("429") || e.getMessage().contains("rate limit"))) {
                                // Rate limited - exponential backoff
                                try {
                                    Thread.sleep(backoffMs);
                                    backoffMs *= 2; // Double the backoff
                                } catch (InterruptedException ie) {
                                    Thread.currentThread().interrupt();
                                    break;
                                }
                            } else if (retryCount >= maxRetries) {
                                // Log and skip this file
                                log.warn("Failed to fetch file {} after {} retries: {}", path, maxRetries, e.getMessage());
                            }
                        }
                    }
                })
            ).get();
        } catch (InterruptedException | java.util.concurrent.ExecutionException e) {
            log.error("Error in parallel file fetch: {}", e.getMessage());
            throw new IOException("Batch file fetch failed", e);
        } finally {
            customPool.shutdown();
        }
        
        log.info("Batch fetched {}/{} files from GitLab", results.size(), filePaths.size());
        return results;
    }
}
