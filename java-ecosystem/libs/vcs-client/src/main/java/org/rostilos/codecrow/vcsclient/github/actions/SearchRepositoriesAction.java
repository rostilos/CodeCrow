package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.github.GitHubConfig;
import org.rostilos.codecrow.vcsclient.github.dto.response.RepositorySearchResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

public class SearchRepositoriesAction {

    private static final Logger log = LoggerFactory.getLogger(SearchRepositoriesAction.class);
    private static final int PAGE_SIZE = 30;
    private final OkHttpClient authorizedOkHttpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public SearchRepositoriesAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    public RepositorySearchResult getRepositories(String owner, int page) throws IOException {
        String url = String.format("%s/users/%s/repos?per_page=%d&page=%d&sort=updated",
                GitHubConfig.API_BASE, owner, PAGE_SIZE, page);
        return fetchRepositories(url);
    }

    public RepositorySearchResult getOrganizationRepositories(String org, int page) throws IOException {
        String url = String.format("%s/orgs/%s/repos?per_page=%d&page=%d&sort=updated",
                GitHubConfig.API_BASE, org, PAGE_SIZE, page);
        return fetchRepositories(url);
    }

    public RepositorySearchResult searchRepositories(String owner, String query, int page) throws IOException {
        String encodedQuery = URLEncoder.encode(query + " user:" + owner, StandardCharsets.UTF_8);
        String url = String.format("%s/search/repositories?q=%s&per_page=%d&page=%d",
                GitHubConfig.API_BASE, encodedQuery, PAGE_SIZE, page);
        return fetchSearchResults(url);
    }

    public int getRepositoriesCount(String owner) throws IOException {
        String url = String.format("%s/users/%s", GitHubConfig.API_BASE, owner);

        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                return tryOrganizationCount(owner);
            }
            JsonNode node = objectMapper.readTree(resp.body().string());
            return node.has("public_repos") ? node.get("public_repos").asInt() : 0;
        }
    }

    private int tryOrganizationCount(String org) throws IOException {
        String url = String.format("%s/orgs/%s", GitHubConfig.API_BASE, org);

        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                return 0;
            }
            JsonNode node = objectMapper.readTree(resp.body().string());
            int publicRepos = node.has("public_repos") ? node.get("public_repos").asInt() : 0;
            int privateRepos = node.has("total_private_repos") ? node.get("total_private_repos").asInt() : 0;
            return publicRepos + privateRepos;
        }
    }

    private RepositorySearchResult fetchRepositories(String url) throws IOException {
        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                throw new IOException("Failed to fetch repositories: " + resp.code() + " - " + body);
            }

            JsonNode root = objectMapper.readTree(resp.body().string());
            List<Map<String, Object>> items = new ArrayList<>();
            
            if (root.isArray()) {
                for (JsonNode node : root) {
                    items.add(parseRepository(node));
                }
            }

            String linkHeader = resp.header("Link");
            boolean hasNext = linkHeader != null && linkHeader.contains("rel=\"next\"");

            return new RepositorySearchResult(items, hasNext, null);
        }
    }

    private RepositorySearchResult fetchSearchResults(String url) throws IOException {
        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                throw new IOException("Failed to search repositories: " + resp.code() + " - " + body);
            }

            JsonNode root = objectMapper.readTree(resp.body().string());
            List<Map<String, Object>> items = new ArrayList<>();
            
            JsonNode itemsNode = root.get("items");
            if (itemsNode != null && itemsNode.isArray()) {
                for (JsonNode node : itemsNode) {
                    items.add(parseRepository(node));
                }
            }

            Integer totalCount = root.has("total_count") ? root.get("total_count").asInt() : null;
            String linkHeader = resp.header("Link");
            boolean hasNext = linkHeader != null && linkHeader.contains("rel=\"next\"");

            return new RepositorySearchResult(items, hasNext, totalCount);
        }
    }

    private Map<String, Object> parseRepository(JsonNode node) {
        return Map.of(
                "id", node.get("id").asLong(),
                "name", node.has("name") ? node.get("name").asText() : "",
                "full_name", node.has("full_name") ? node.get("full_name").asText() : "",
                "description", node.has("description") && !node.get("description").isNull() 
                        ? node.get("description").asText() : "",
                "private", node.has("private") && node.get("private").asBoolean(),
                "html_url", node.has("html_url") ? node.get("html_url").asText() : "",
                "default_branch", node.has("default_branch") ? node.get("default_branch").asText() : "main"
        );
    }
}
