package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.github.GitHubConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.regex.Pattern;

/** Retrieves GitHub comparison metadata for one exact commit pair. */
public class GetCommitComparisonAction {
    private static final Logger log = LoggerFactory.getLogger(GetCommitComparisonAction.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    private static final Pattern EXACT_COMMIT =
            Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");

    private final OkHttpClient authorizedOkHttpClient;

    public GetCommitComparisonAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Loads JSON comparison metadata, including {@code merge_base_commit.sha},
     * without resolving either side through a mutable branch name.
     */
    public JsonNode getCommitComparison(
            String owner,
            String repo,
            String baseCommitHash,
            String headCommitHash) throws IOException {
        requireExactCommit(baseCommitHash, "baseCommitHash");
        requireExactCommit(headCommitHash, "headCommitHash");

        String basehead = baseCommitHash + "..." + headCommitHash;
        String apiUrl = String.format(
                "%s/repos/%s/%s/compare/%s",
                GitHubConfig.API_BASE,
                owner,
                repo,
                basehead);
        Request request = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/vnd.github+json")
                .header("X-GitHub-Api-Version", "2022-11-28")
                .get()
                .build();

        try (Response response = authorizedOkHttpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String body = response.body() != null ? response.body().string() : "";
                String message = String.format(
                        "GitHub returned non-success response %d for comparison URL %s: %s",
                        response.code(),
                        apiUrl,
                        body);
                log.warn(message);
                throw new IOException(message);
            }
            String body = response.body() != null ? response.body().string() : "{}";
            return objectMapper.readTree(body);
        }
    }

    private static void requireExactCommit(String value, String field) {
        if (value == null || !EXACT_COMMIT.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " must be an exact commit SHA");
        }
    }
}
