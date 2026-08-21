package org.rostilos.codecrow.vcsclient.gitlab.api;

import com.fasterxml.jackson.databind.JsonNode;
import okhttp3.Response;

import java.io.IOException;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;

/**
 * Focused GitLab repository operations used by the shared client.
 */
public final class GitLabRepositoryApi {

    private static final int MAX_TREE_REQUESTS = 10_000;
    private static final long MAX_TREE_ENTRIES = 2_000_000L;
    private static final long MIN_TREE_ENTRIES = 256L;

    private final GitLabApiContext api;

    public GitLabRepositoryApi(GitLabApiContext api) {
        this.api = api;
    }

    public boolean fileExists(
            String namespace,
            String project,
            String branchOrCommit,
            String filePath
    ) throws IOException {
        String url = api.projectUrl(namespace, project)
                + "/repository/files/" + api.encode(filePath)
                + "?ref=" + api.encode(branchOrCommit);
        try (Response response = api.execute(api.head(url))) {
            if (response.code() == 404) {
                return false;
            }
            if (!response.isSuccessful()) {
                throw api.error("check file existence", response);
            }
            return true;
        }
    }

    public String getTree(
            String namespace,
            String project,
            String branchOrCommit,
            String directoryPath
    ) throws IOException {
        StringBuilder url = new StringBuilder(api.projectUrl(namespace, project))
                .append("/repository/tree?ref=")
                .append(api.encode(branchOrCommit));
        if (directoryPath != null && !directoryPath.isBlank()) {
            url.append("&path=").append(api.encode(directoryPath));
        }
        try (Response response = api.execute(api.get(url.toString()))) {
            if (!response.isSuccessful()) {
                throw api.error("get repository tree", response);
            }
            return api.bodyOr(response, "[]");
        }
    }

    public List<String> listFiles(
            String namespace,
            String project,
            String commit,
            int maxFiles
    ) throws IOException {
        if (maxFiles <= 0) throw new IllegalArgumentException("maxFiles must be positive");
        final int pageSize = 100;
        TreeSet<String> files = new TreeSet<>();
        Set<String> visitedPages = new LinkedHashSet<>();
        long maxEntries = Math.min(
                MAX_TREE_ENTRIES,
                Math.max(MIN_TREE_ENTRIES, (long) maxFiles * 4L));
        long traversedEntries = 0;
        int requests = 0;
        String treeEndpoint = api.projectUrl(namespace, project) + "/repository/tree";
        String url = treeEndpoint
                + "?ref=" + api.encode(commit)
                + "&recursive=true&per_page=" + pageSize
                + "&pagination=keyset";
        while (url != null) {
            if (!url.startsWith(treeEndpoint)) {
                throw new IOException("Repository tree pagination left the provider endpoint");
            }
            if (!visitedPages.add(url)) {
                throw new IOException("Repository tree pagination repeated a page");
            }
            requests++;
            if (requests > MAX_TREE_REQUESTS) {
                throw new IOException(
                        "Repository tree exceeds the " + MAX_TREE_REQUESTS
                                + "-request traversal limit");
            }
            try (Response response = api.execute(api.get(url))) {
                if (!response.isSuccessful()) {
                    throw api.error("list repository files", response);
                }
                JsonNode entries = api.objectMapper().readTree(
                        api.bodyOr(response, "[]"));
                if (!entries.isArray()) {
                    throw new IOException("Repository tree response is not an array");
                }
                traversedEntries += entries.size();
                if (traversedEntries > maxEntries) {
                    throw new IOException(
                            "Repository tree exceeds the " + maxEntries
                                    + "-entry traversal limit");
                }
                for (JsonNode entry : entries) {
                    if (!isRegularFile(entry)) continue;
                    String path = entry.path("path").asText("");
                    if (path.isBlank()) continue;
                    files.add(path);
                    if (files.size() > maxFiles) {
                        throw new IOException(
                                "Repository tree exceeds the " + maxFiles
                                        + "-file inventory limit");
                    }
                }
                url = nextPageUrl(response);
            }
        }
        return List.copyOf(files);
    }

    private static boolean isRegularFile(JsonNode entry) {
        if (!"blob".equals(entry.path("type").asText(""))) return false;
        String mode = entry.path("mode").asText("");
        return "100644".equals(mode) || "100755".equals(mode);
    }

    private static String nextPageUrl(Response response) throws IOException {
        String link = response.header("Link");
        if (link == null || link.isBlank()) return null;
        for (String part : link.split(",")) {
            if (!part.contains("rel=\"next\"") && !part.contains("rel=next")) continue;
            int start = part.indexOf('<');
            int end = part.indexOf('>', start + 1);
            if (start < 0 || end <= start + 1) {
                throw new IOException("Repository tree returned an invalid next-page link");
            }
            return part.substring(start + 1, end);
        }
        return null;
    }
}
