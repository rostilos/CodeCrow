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
        // First, try the installation repositories endpoint.
        // This only works with GitHub App installation tokens and returns
        // ONLY the repositories that were selected during app installation.
        RepositorySearchResult installationResult = tryInstallationRepositories(page);
        if (installationResult != null) {
            log.info("Using GitHub App installation repositories: {} repos for page {}", 
                    installationResult.items().size(), page);
            return installationResult;
        }
        
        // Fallback to user repos (OAuth tokens, PATs)
        log.debug("Falling back to user repositories endpoint for owner: {}", owner);
        String url = String.format("%s/users/%s/repos?per_page=%d&page=%d&sort=updated",
                GitHubConfig.API_BASE, owner, PAGE_SIZE, page);
        return fetchRepositories(url);
    }
    
    /**
     * Try to fetch repositories from the /installation/repositories endpoint.
     * This endpoint only works with GitHub App installation tokens.
     * Returns null if not using an installation token.
     */
    private RepositorySearchResult tryInstallationRepositories(int page) {
        String url = String.format("%s/installation/repositories?per_page=%d&page=%d",
                GitHubConfig.API_BASE, PAGE_SIZE, page);
        
        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();
        
        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            log.debug("Installation repositories endpoint returned: {}", resp.code());
            
            if (resp.isSuccessful()) {
                JsonNode root = objectMapper.readTree(resp.body().string());
                List<Map<String, Object>> items = new ArrayList<>();
                
                JsonNode reposArray = root.get("repositories");
                Integer totalCount = root.has("total_count") ? root.get("total_count").asInt() : null;
                
                log.info("GitHub App installation returned {} repositories (total_count={})", 
                        reposArray != null ? reposArray.size() : 0, totalCount);
                
                if (reposArray != null && reposArray.isArray()) {
                    for (JsonNode node : reposArray) {
                        items.add(parseRepository(node));
                    }
                }
                
                String linkHeader = resp.header("Link");
                boolean hasNext = linkHeader != null && linkHeader.contains("rel=\"next\"");
                
                return new RepositorySearchResult(items, hasNext, totalCount);
            } else {
                // 403/401 means not an installation token
                String errorBody = resp.body() != null ? resp.body().string() : "";
                log.debug("Installation endpoint returned {}: {}", resp.code(), errorBody);
                return null;
            }
        } catch (IOException e) {
            log.debug("Installation endpoint failed: {}", e.getMessage());
            return null;
        }
    }

    public RepositorySearchResult getOrganizationRepositories(String org, int page) throws IOException {
        // For organization repos, also try installation endpoint first
        RepositorySearchResult installationResult = tryInstallationRepositories(page);
        if (installationResult != null) {
            log.info("Using GitHub App installation repositories for org: {} repos for page {}", 
                    installationResult.items().size(), page);
            return installationResult;
        }
        
        String url = String.format("%s/orgs/%s/repos?per_page=%d&page=%d&sort=updated",
                GitHubConfig.API_BASE, org, PAGE_SIZE, page);
        return fetchRepositories(url);
    }

    public RepositorySearchResult searchRepositories(String owner, String query, int page) throws IOException {
        // For installation tokens, we need to search within installation repos only.
        // GitHub search API doesn't support filtering by installation, so we fetch
        // all installation repos and filter client-side for search queries.
        RepositorySearchResult installationResult = tryInstallationRepositoriesWithSearch(query, page);
        if (installationResult != null) {
            log.info("Searched within GitHub App installation repositories: {} matching repos", 
                    installationResult.items().size());
            return installationResult;
        }
        
        // Fallback to GitHub search API
        String encodedQuery = URLEncoder.encode(query + " user:" + owner, StandardCharsets.UTF_8);
        String url = String.format("%s/search/repositories?q=%s&per_page=%d&page=%d",
                GitHubConfig.API_BASE, encodedQuery, PAGE_SIZE, page);
        return fetchSearchResults(url);
    }
    
    /**
     * Search within installation repositories by fetching all and filtering.
     * Returns null if not using an installation token.
     */
    private RepositorySearchResult tryInstallationRepositoriesWithSearch(String query, int page) {
        String lowerQuery = query.toLowerCase();
        String url = String.format("%s/installation/repositories?per_page=100&page=1",
                GitHubConfig.API_BASE);
        
        Request req = new Request.Builder()
                .url(url)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();
        
        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                return null;
            }
            
            JsonNode root = objectMapper.readTree(resp.body().string());
            JsonNode reposArray = root.get("repositories");
            
            if (reposArray == null || !reposArray.isArray()) {
                return new RepositorySearchResult(new ArrayList<>(), false, 0);
            }
            
            // Filter repos by query (name or description)
            List<Map<String, Object>> matchingItems = new ArrayList<>();
            for (JsonNode node : reposArray) {
                String name = node.has("name") ? node.get("name").asText().toLowerCase() : "";
                String fullName = node.has("full_name") ? node.get("full_name").asText().toLowerCase() : "";
                String description = node.has("description") && !node.get("description").isNull() 
                        ? node.get("description").asText().toLowerCase() : "";
                
                if (name.contains(lowerQuery) || fullName.contains(lowerQuery) || description.contains(lowerQuery)) {
                    matchingItems.add(parseRepository(node));
                }
            }
            
            // Simple pagination
            int startIdx = (page - 1) * PAGE_SIZE;
            int endIdx = Math.min(startIdx + PAGE_SIZE, matchingItems.size());
            
            if (startIdx >= matchingItems.size()) {
                return new RepositorySearchResult(new ArrayList<>(), false, matchingItems.size());
            }
            
            List<Map<String, Object>> pageItems = matchingItems.subList(startIdx, endIdx);
            boolean hasNext = endIdx < matchingItems.size();
            
            return new RepositorySearchResult(pageItems, hasNext, matchingItems.size());
            
        } catch (IOException e) {
            log.debug("Installation search failed: {}", e.getMessage());
            return null;
        }
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
