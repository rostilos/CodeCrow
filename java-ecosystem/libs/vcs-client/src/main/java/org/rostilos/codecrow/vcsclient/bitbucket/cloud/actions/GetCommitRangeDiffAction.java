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
 * Action to retrieve diff between two commits (commit range) from Bitbucket Cloud.
 * Used for incremental/delta analysis to get only changes since the last analyzed commit.
 */
public class GetCommitRangeDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetCommitRangeDiffAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public GetCommitRangeDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Fetches the diff between two commits.
     * 
     * Bitbucket API: GET /repositories/{workspace}/{repo_slug}/diff/{spec}
     * where spec can be "commit1..commit2" format
     *
     * @param workspace workspace or team slug
     * @param repoSlug repository slug
     * @param baseCommitHash the base commit (previously analyzed commit)
     * @param headCommitHash the head commit (current commit to analyze)
     * @return raw unified diff between the two commits
     * @throws IOException on network / parsing errors
     */
    public String getCommitRangeDiff(String workspace, String repoSlug, String baseCommitHash, String headCommitHash) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse("");
        
        // Bitbucket uses the spec format: base..head
        String spec = baseCommitHash + ".." + headCommitHash;
        String apiUrl = String.format("%s/repositories/%s/%s/diff/%s",
                BitbucketCloudConfig.BITBUCKET_API_BASE, ws, repoSlug, spec);

        log.info("Fetching commit range diff: {} from {} to {}", repoSlug, baseCommitHash.substring(0, 7), headCommitHash.substring(0, 7));

        Request req = new Request.Builder()
                .url(apiUrl)
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("Bitbucket returned non-success response %d for commit range diff URL %s: %s",
                        resp.code(), apiUrl, body);
                log.warn(msg);
                throw new IOException(msg);
            }
            String diff = resp.body() != null ? resp.body().string() : "";
            log.info("Retrieved commit range diff: {} chars", diff.length());
            return diff;
        } catch (IOException e) {
            log.error("Failed to get commit range diff: {}", e.getMessage(), e);
            throw e;
        }
    }
}
