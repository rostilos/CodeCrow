package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.response.RepositorySearchResult;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

//TODO: bstract or a single client class for actions
public class SearchBitbucketCloudReposAction {
    private static final int PAGE_LENGTH = 50;
    private final OkHttpClient authorizedOkHttpClient;
    private final ObjectMapper objectMapper;

    public SearchBitbucketCloudReposAction(
            OkHttpClient authorizedOkHttpClient
    ) {
        this.objectMapper = new ObjectMapper();
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    public RepositorySearchResult getRepositories(String workspace, int page) throws IOException {
        String url = BitbucketCloudConfig.BITBUCKET_API_BASE + "/repositories/" + workspace + "?pagelen=" + PAGE_LENGTH;
        if (page >= 1) {
            url = url + "&page=" + page;
        }
        return fetchRepositoriesWithPagination(url, Integer.MAX_VALUE, page);
    }

    public RepositorySearchResult searchRepositories(String workspace, String query, int page) throws IOException {
        String encodedQuery = URLEncoder.encode("name~\"" + query + "\"", StandardCharsets.UTF_8);
        String url = BitbucketCloudConfig.BITBUCKET_API_BASE + "/repositories/" + workspace + "?q=" + encodedQuery + "&pagelen=" + PAGE_LENGTH;
        if (page >= 1) {
            url = url + "&page=" + page;
        }
        return fetchRepositoriesWithPagination(url, PAGE_LENGTH, page);
    }

    private RepositorySearchResult fetchRepositoriesWithPagination(String url, int limit, int currentPage) throws IOException {
        List<Repository> repositories = new ArrayList<>(Math.min(limit, PAGE_LENGTH));
        Integer totalSize = null;
        boolean hasNext;
        boolean hasPrevious = currentPage > 1;

        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response response = authorizedOkHttpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                throw new RuntimeException("Failed to fetch repositories: " +
                        response.code() + " - " + errorBody);
            }

            assert response.body() != null;
            String responseBody = response.body().string();
            JsonNode rootNode = objectMapper.readTree(responseBody);
            JsonNode valuesNode = rootNode.get("values");

            if (rootNode.has("size")) {
                totalSize = rootNode.get("size").asInt();
            }

            if (valuesNode != null && valuesNode.isArray()) {
                for (JsonNode repoNode : valuesNode) {
                    repositories.add(parseRepository(repoNode));
                    if (repositories.size() >= limit) {
                        break;
                    }
                }
            }

            JsonNode nextNode = rootNode.get("next");
            String nextPageUrl = (nextNode != null) ? nextNode.asText() : null;
            hasNext = nextPageUrl != null;
        }

        return new RepositorySearchResult(
                repositories,
                currentPage,
                PAGE_LENGTH,
                repositories.size(),
                totalSize,
                hasNext,
                hasPrevious
        );
    }

    public int getRepositoriesCount(String workspace) throws IOException {
        String url = BitbucketCloudConfig.BITBUCKET_API_BASE + "/repositories/" + workspace + "?pagelen=" + PAGE_LENGTH;
        int totalSize = 0;
        Request request = new Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response response = authorizedOkHttpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String errorBody = response.body() != null ? response.body().string() : "";
                throw new RuntimeException("Failed to fetch repositories: " +
                        response.code() + " - " + errorBody);
            }

            assert response.body() != null;
            String responseBody = response.body().string();
            JsonNode rootNode = objectMapper.readTree(responseBody);

            if (rootNode.has("size")) {
                totalSize = rootNode.get("size").asInt();
            }
        }
        return totalSize;
    }

    private Repository parseRepository(JsonNode repoNode) {
        Repository repo = new Repository();
        repo.setName(repoNode.get("name").asText());
        repo.setFullName(repoNode.get("full_name").asText());
        repo.setUuid(repoNode.get("uuid").asText());

        JsonNode linksNode = repoNode.get("links");
        if (linksNode != null && linksNode.has("clone")) {
            JsonNode cloneNode = linksNode.get("clone");
            if (cloneNode.isArray()) {
                for (JsonNode clone : cloneNode) {
                    String name = clone.get("name").asText();
                    String href = clone.get("href").asText();
                    if ("https".equals(name)) {
                        repo.setCloneUrlHttps(href);
                    }
                }
            }
        }

        return repo;
    }

    public static class Repository {
        private String name;
        private String fullName;
        private String uuid;
        private String cloneUrlHttps;

        public String getName() {
            return name;
        }

        public void setName(String name) {
            this.name = name;
        }

        public String getFullName() {
            return fullName;
        }

        public void setFullName(String fullName) {
            this.fullName = fullName;
        }

        public String getUuid() {
            return uuid;
        }

        public void setUuid(String uuid) {
            this.uuid = uuid;
        }

        public String getCloneUrlHttps() {
            return cloneUrlHttps;
        }

        public void setCloneUrlHttps(String cloneUrlHttps) {
            this.cloneUrlHttps = cloneUrlHttps;
        }

        @Override
        public String toString() {
            return String.format("Repository{name='%s', fullName='%s'}",
                    name, fullName);
        }
    }
}