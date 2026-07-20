package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;

import java.io.IOException;
import java.util.Objects;
import java.util.regex.Pattern;

/** Resolves Bitbucket Cloud's best common ancestor for two exact commits. */
public final class GetMergeBaseAction {
    private static final Pattern EXACT_REVISION =
            Pattern.compile("(?:[0-9a-f]{40}|[0-9a-f]{64})");
    private final OkHttpClient authorizedHttpClient;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public GetMergeBaseAction(OkHttpClient authorizedHttpClient) {
        this.authorizedHttpClient = Objects.requireNonNull(
                authorizedHttpClient, "authorizedHttpClient");
    }

    public String getMergeBase(
            String workspace,
            String repoSlug,
            String baseCommit,
            String headCommit) throws IOException {
        requireExactRevision(baseCommit, "baseCommit");
        requireExactRevision(headCommit, "headCommit");
        String revisionSpec = baseCommit + ".." + headCommit;
        String apiUrl = String.format(
                "%s/repositories/%s/%s/merge-base/%s",
                BitbucketCloudConfig.BITBUCKET_API_BASE,
                workspace,
                repoSlug,
                revisionSpec);
        Request request = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response response = authorizedHttpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String body = response.body() != null ? response.body().string() : "";
                throw new IOException("Bitbucket returned " + response.code()
                        + " while resolving exact merge base: " + body);
            }
            String body = response.body() != null ? response.body().string() : "{}";
            JsonNode root = objectMapper.readTree(body);
            String mergeBase = root.path("hash").asText(null);
            if (mergeBase == null || mergeBase.isBlank()) {
                throw new IOException("Bitbucket merge-base response omitted hash");
            }
            return mergeBase;
        }
    }

    private static void requireExactRevision(String value, String field) {
        if (value == null || !EXACT_REVISION.matcher(value).matches()) {
            throw new IllegalArgumentException(field + " must be an exact lowercase commit SHA");
        }
    }
}
