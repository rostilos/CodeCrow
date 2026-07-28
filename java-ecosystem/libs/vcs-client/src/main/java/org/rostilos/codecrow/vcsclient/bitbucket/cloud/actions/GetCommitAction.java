package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;

import java.io.IOException;
import java.util.Optional;

/**
 * Resolves a Bitbucket Cloud commit reference to the provider's canonical hash.
 *
 * <p>Pull-request metadata may contain abbreviated commit hashes. Review
 * snapshots must use the full immutable object id, so callers resolve those
 * references through the commit endpoint before constructing an analysis.</p>
 */
public class GetCommitAction {
    private final OkHttpClient authorizedOkHttpClient;
    private final ObjectMapper objectMapper;
    private final String apiBaseUrl;

    public GetCommitAction(OkHttpClient authorizedOkHttpClient) {
        this(authorizedOkHttpClient, BitbucketCloudConfig.BITBUCKET_API_BASE);
    }

    GetCommitAction(OkHttpClient authorizedOkHttpClient, String apiBaseUrl) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
        this.objectMapper = new ObjectMapper();
        this.apiBaseUrl = apiBaseUrl.endsWith("/")
                ? apiBaseUrl.substring(0, apiBaseUrl.length() - 1)
                : apiBaseUrl;
    }

    public String resolveCommitHash(
            String workspace,
            String repoSlug,
            String commitReference) throws IOException {
        if (commitReference == null || commitReference.isBlank()) {
            throw new IOException("Bitbucket commit reference is required");
        }

        String ws = Optional.ofNullable(workspace).orElse("");
        String apiUrl = String.format("%s/repositories/%s/%s/commit/%s",
                apiBaseUrl, ws, repoSlug, commitReference);
        Request request = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response response = authorizedOkHttpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String body = response.body() != null ? response.body().string() : "";
                throw new IOException(String.format(
                        "Bitbucket returned non-success response %d while resolving commit: %s",
                        response.code(), body));
            }

            String responseBody = response.body() != null ? response.body().string() : "{}";
            JsonNode json = objectMapper.readTree(responseBody);
            String canonicalHash = json.path("hash").asText("");
            if (canonicalHash.isBlank()) {
                throw new IOException("Bitbucket commit response did not contain a canonical hash");
            }
            return canonicalHash;
        }
    }
}
