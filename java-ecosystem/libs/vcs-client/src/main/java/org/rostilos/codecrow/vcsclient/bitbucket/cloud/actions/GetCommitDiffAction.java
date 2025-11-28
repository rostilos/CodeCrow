package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Optional;

/**
 * Action to retrieve commit diff from Bitbucket Cloud.
 * Returns the raw unified diff for a specific commit as a String.
 */
public class GetCommitDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetCommitDiffAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public GetCommitDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Fetches the raw diff for a commit.
     *
     * @param workspace workspace or team slug
     * @param repoSlug repository slug
     * @param commitHash commit hash
     * @return raw unified diff as returned by Bitbucket API
     * @throws IOException on network / parsing errors
     */
    public String getCommitDiff(String workspace, String repoSlug, String commitHash) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse("");
        String apiUrl = String.format("%s/repositories/%s/%s/diff/%s",
                BitbucketCloudConfig.BITBUCKET_API_BASE, ws, repoSlug, commitHash);

        Request req = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("Bitbucket returned non-success response %d for URL %s: %s",
                        resp.code(), apiUrl, body);
                log.warn(msg);
                throw new IOException(msg);
            }
            return resp.body() != null ? resp.body().string() : "";
        } catch (IOException e) {
            log.error("Failed to get commit diff: {}", e.getMessage(), e);
            throw e;
        }
    }
}

