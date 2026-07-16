package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.vcsclient.diff.DiffAcquisitionException;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventoryParser;
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
        String displayWorkspace = ws.isEmpty() ? "(no-workspace)" : ws;
        
        // Bitbucket's two-commit spec is intentionally the reverse of
        // `git diff`: the first commit is the source containing the changes
        // and the second is the destination to compare against. Preserve this
        // method's base-to-head contract by sending head..base.
        String spec = headCommitHash + ".." + baseCommitHash;
        String apiUrl = String.format("%s/repositories/%s/%s/diff/%s",
                BitbucketCloudConfig.BITBUCKET_API_BASE, ws, repoSlug, spec);

        log.info("Fetching commit range diff: {}/{} from {} to {}",
                displayWorkspace, repoSlug,
                baseCommitHash.length() >= 7 ? baseCommitHash.substring(0, 7) : baseCommitHash,
                headCommitHash.length() >= 7 ? headCommitHash.substring(0, 7) : headCommitHash);

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
            ResponseBody body = resp.body();
            if (body == null) {
                throw new DiffAcquisitionException(
                        ExactDiffInventory.GapType.PATCH_UNAVAILABLE,
                        "Bitbucket compare response body is missing");
            }
            String diff = body.string();
            requireCompleteInventory(diff);
            log.info("Retrieved commit range diff: {} chars", diff.length());
            return diff;
        } catch (IOException e) {
            log.error("Failed to get commit range diff: {}", e.getMessage(), e);
            throw e;
        }
    }

    private static void requireCompleteInventory(String rawDiff)
            throws DiffAcquisitionException {
        ExactDiffInventory inventory = new ExactDiffInventoryParser().parse(rawDiff);
        if (inventory.completeness() == ExactDiffInventory.Completeness.COMPLETE) {
            return;
        }
        ExactDiffInventory.GapType reason = inventory.gaps().isEmpty()
                ? ExactDiffInventory.GapType.MALFORMED
                : inventory.gaps().get(0).type();
        throw new DiffAcquisitionException(
                reason,
                "Bitbucket compare response is not a complete unified diff");
    }
}
