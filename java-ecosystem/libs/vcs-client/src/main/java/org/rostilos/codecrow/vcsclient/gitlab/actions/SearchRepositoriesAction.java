package org.rostilos.codecrow.vcsclient.gitlab.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig;
import org.rostilos.codecrow.vcsclient.gitlab.dto.response.RepositorySearchResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Action to search GitLab repositories.
 */
public class SearchRepositoriesAction {

    private static final Logger log = LoggerFactory.getLogger(SearchRepositoriesAction.class);
    private static final int PAGE_SIZE = 30;
    private final OkHttpClient authorizedOkHttpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public SearchRepositoriesAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Get all repositories accessible to the authenticated user.
     */
    public RepositorySearchResult getRepositories(String groupId, int page) throws IOException {
        String url;
        if (groupId != null && !groupId.isBlank()) {
            // Get repositories within a specific group
            String encodedGroup = URLEncoder.encode(groupId, StandardCharsets.UTF_8);
            url = String.format("%s/groups/%s/projects?per_page=%d&page=%d&order_by=updated_at&sort=desc&include_subgroups=true",
                    GitLabConfig.API_BASE, encodedGroup, PAGE_SIZE, page);
        } else {
            // Get all repositories accessible to the user
            url = String.format("%s/projects?per_page=%d&page=%d&order_by=updated_at&sort=desc&membership=true",
                    GitLabConfig.API_BASE, PAGE_SIZE, page);
        }
        return fetchRepositories(url);
    }

    /**
     * Get repositories for a specific group/namespace.
     */
    public RepositorySearchResult getGroupRepositories(String groupId, int page) throws IOException {
        String encodedGroup = URLEncoder.encode(groupId, StandardCharsets.UTF_8);
        String url = String.format("%s/groups/%s/projects?per_page=%d&page=%d&order_by=updated_at&sort=desc&include_subgroups=true",
                GitLabConfig.API_BASE, encodedGroup, PAGE_SIZE, page);
        return fetchRepositories(url);
    }

    /**
     * Search repositories by name.
     */
    public RepositorySearchResult searchRepositories(String groupId, String query, int page) throws IOException {
        String encodedQuery = URLEncoder.encode(query, StandardCharsets.UTF_8);
        String url;
        
        if (groupId != null && !groupId.isBlank()) {
            String encodedGroup = URLEncoder.encode(groupId, StandardCharsets.UTF_8);
            url = String.format("%s/groups/%s/projects?search=%s&per_page=%d&page=%d&order_by=updated_at&sort=desc&include_subgroups=true",
                    GitLabConfig.API_BASE, encodedGroup, encodedQuery, PAGE_SIZE, page);
        } else {
            url = String.format("%s/projects?search=%s&per_page=%d&page=%d&order_by=updated_at&sort=desc&membership=true",
                    GitLabConfig.API_BASE, encodedQuery, PAGE_SIZE, page);
        }
        
        return fetchRepositories(url);
    }

    /**
     * Get total count of repositories in a group.
     */
    public int getRepositoriesCount(String groupId) throws IOException {
        if (groupId == null || groupId.isBlank()) {
            // Get count of all accessible repositories
            String url = String.format("%s/projects?per_page=1&membership=true", GitLabConfig.API_BASE);
            return fetchTotalCount(url);
        } else {
            String encodedGroup = URLEncoder.encode(groupId, StandardCharsets.UTF_8);
            String url = String.format("%s/groups/%s/projects?per_page=1&include_subgroups=true", 
                    GitLabConfig.API_BASE, encodedGroup);
            return fetchTotalCount(url);
        }
    }

    private int fetchTotalCount(String url) throws IOException {
        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                log.warn("Failed to get repository count: {}", resp.code());
                return 0;
            }
            
            // GitLab returns total count in X-Total header
            String totalHeader = resp.header("X-Total");
            if (totalHeader != null) {
                try {
                    return Integer.parseInt(totalHeader);
                } catch (NumberFormatException e) {
                    log.warn("Failed to parse X-Total header: {}", totalHeader);
                }
            }
            return 0;
        }
    }

    private RepositorySearchResult fetchRepositories(String url) throws IOException {
        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                log.warn("Failed to fetch repositories: {} - {}", resp.code(), body);
                return new RepositorySearchResult(List.of(), false, 0);
            }

            String body = resp.body() != null ? resp.body().string() : "[]";
            JsonNode root = objectMapper.readTree(body);
            
            List<Map<String, Object>> items = new ArrayList<>();
            if (root.isArray()) {
                for (JsonNode node : root) {
                    items.add(parseRepository(node));
                }
            }

            // Check for next page using X-Next-Page header
            String nextPageHeader = resp.header("X-Next-Page");
            boolean hasNext = nextPageHeader != null && !nextPageHeader.isBlank();
            
            // Get total count from X-Total header
            String totalHeader = resp.header("X-Total");
            Integer totalCount = null;
            if (totalHeader != null) {
                try {
                    totalCount = Integer.parseInt(totalHeader);
                } catch (NumberFormatException e) {
                    log.debug("Failed to parse X-Total header: {}", totalHeader);
                }
            }

            return new RepositorySearchResult(items, hasNext, totalCount);
        }
    }

    private Map<String, Object> parseRepository(JsonNode node) {
        Map<String, Object> repo = new HashMap<>();
        repo.put("id", node.has("id") ? node.get("id").asLong() : null);
        repo.put("name", node.has("name") ? node.get("name").asText() : null);
        repo.put("full_name", node.has("path_with_namespace") ? node.get("path_with_namespace").asText() : null);
        repo.put("description", node.has("description") && !node.get("description").isNull() 
                ? node.get("description").asText() : null);
        repo.put("html_url", node.has("web_url") ? node.get("web_url").asText() : null);
        repo.put("clone_url", node.has("http_url_to_repo") ? node.get("http_url_to_repo").asText() : null);
        repo.put("ssh_url", node.has("ssh_url_to_repo") ? node.get("ssh_url_to_repo").asText() : null);
        repo.put("default_branch", node.has("default_branch") ? node.get("default_branch").asText() : "main");
        repo.put("private", node.has("visibility") ? !"public".equals(node.get("visibility").asText()) : true);
        repo.put("updated_at", node.has("last_activity_at") ? node.get("last_activity_at").asText() : null);
        repo.put("created_at", node.has("created_at") ? node.get("created_at").asText() : null);
        
        // GitLab uses namespace for owner info
        if (node.has("namespace")) {
            JsonNode ns = node.get("namespace");
            Map<String, Object> owner = new HashMap<>();
            owner.put("login", ns.has("path") ? ns.get("path").asText() : null);
            owner.put("avatar_url", ns.has("avatar_url") && !ns.get("avatar_url").isNull() 
                    ? ns.get("avatar_url").asText() : null);
            repo.put("owner", owner);
        }
        
        return repo;
    }
}
