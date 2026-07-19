package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.HttpUrl;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Loads the complete paginated file/count inventory for an exact Bitbucket range. */
public final class GetCommitRangeDiffStatAction {
    private static final Pattern EXACT_REVISION =
            Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");

    private final OkHttpClient client;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public GetCommitRangeDiffStatAction(OkHttpClient client) {
        this.client = client;
    }

    public List<FileStat> getFileStats(
            String workspace,
            String repository,
            String mergeBase,
            String head) throws IOException {
        requireExact(mergeBase, "mergeBase");
        requireExact(head, "head");
        String expectedPathPrefix = "/2.0/repositories/" + workspace + "/" + repository + "/diffstat/";
        String next = BitbucketCloudConfig.BITBUCKET_API_BASE
                + "/repositories/" + workspace + "/" + repository
                + "/diffstat/" + head + ".." + mergeBase;
        Set<String> visitedPages = new HashSet<>();
        Set<String> uniquePaths = new HashSet<>();
        List<FileStat> files = new ArrayList<>();

        while (next != null) {
            if (!visitedPages.add(next)) {
                throw new IOException("Bitbucket diffstat pagination contains a cycle");
            }
            validatePageUrl(next, expectedPathPrefix);
            Request request = new Request.Builder()
                    .url(next)
                    .header("Accept", "application/json")
                    .get()
                    .build();
            try (Response response = client.newCall(request).execute()) {
                if (!response.isSuccessful()) {
                    throw new IOException("Bitbucket returned " + response.code()
                            + " while loading exact diffstat");
                }
                JsonNode root = objectMapper.readTree(
                        response.body() != null ? response.body().bytes() : new byte[0]);
                JsonNode values = root.get("values");
                if (values == null || !values.isArray()) {
                    throw new IOException("Bitbucket diffstat response omitted values");
                }
                for (JsonNode value : values) {
                    String status = value.path("status").asText("");
                    String path = "removed".equals(status)
                            ? value.path("old").path("path").asText("")
                            : value.path("new").path("path").asText("");
                    if (status.isBlank() || path.isBlank()) {
                        throw new IOException("Bitbucket diffstat entry omitted status or path");
                    }
                    if (!uniquePaths.add(path)) {
                        throw new IOException("Bitbucket diffstat contains duplicate path: " + path);
                    }
                    files.add(new FileStat(
                            path,
                            requireNonNegativeCount(value, "lines_added"),
                            requireNonNegativeCount(value, "lines_removed")));
                }
                JsonNode nextNode = root.get("next");
                if (nextNode == null || nextNode.isNull()) {
                    next = null;
                } else if (!nextNode.isTextual() || nextNode.asText().isBlank()) {
                    throw new IOException("Bitbucket diffstat pagination has malformed next URL");
                } else {
                    next = nextNode.asText();
                }
            }
        }
        return List.copyOf(files);
    }

    private static long requireNonNegativeCount(JsonNode entry, String field) throws IOException {
        JsonNode value = entry.get(field);
        if (value == null || !value.isIntegralNumber()
                || !value.canConvertToLong() || value.longValue() < 0) {
            throw new IOException("Bitbucket diffstat entry has invalid " + field);
        }
        return value.longValue();
    }

    private static void validatePageUrl(String value, String expectedPathPrefix) throws IOException {
        HttpUrl url = HttpUrl.parse(value);
        if (url == null || !"https".equals(url.scheme())
                || !"api.bitbucket.org".equals(url.host())
                || !url.encodedPath().startsWith(expectedPathPrefix)) {
            throw new IOException("Bitbucket diffstat pagination returned an unsafe next URL");
        }
    }

    private static void requireExact(String value, String field) {
        if (value == null || !EXACT_REVISION.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " must be an exact lowercase commit SHA");
        }
    }

    public record FileStat(String path, long linesAdded, long linesRemoved) {}
}
