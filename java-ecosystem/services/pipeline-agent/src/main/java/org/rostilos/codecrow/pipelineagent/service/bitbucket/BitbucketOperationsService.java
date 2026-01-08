package org.rostilos.codecrow.pipelineagent.service.bitbucket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.CheckFileExistsInBranchAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetCommitDiffAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetPullRequestDiffAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;

/**
 * Bitbucket implementation of VcsOperationsService.
 * Delegates to Bitbucket-specific action classes for API calls.
 */
@Service
public class BitbucketOperationsService implements VcsOperationsService {

    private static final Logger log = LoggerFactory.getLogger(BitbucketOperationsService.class);
    private static final String BITBUCKET_API_BASE = "https://api.bitbucket.org/2.0";
    private static final ObjectMapper objectMapper = new ObjectMapper();

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.BITBUCKET_CLOUD;
    }

    @Override
    public String getCommitDiff(OkHttpClient client, String workspace, String repoSlug, String commitHash) throws IOException {
        GetCommitDiffAction action = new GetCommitDiffAction(client);
        return action.getCommitDiff(workspace, repoSlug, commitHash);
    }

    @Override
    public String getPullRequestDiff(OkHttpClient client, String workspace, String repoSlug, String prNumber) throws IOException {
        GetPullRequestDiffAction action = new GetPullRequestDiffAction(client);
        return action.getPullRequestDiff(workspace, repoSlug, prNumber);
    }

    @Override
    public String getCommitRangeDiff(OkHttpClient client, String workspace, String repoSlug, String baseCommitHash, String headCommitHash) throws IOException {
        GetCommitRangeDiffAction action = new GetCommitRangeDiffAction(client);
        return action.getCommitRangeDiff(workspace, repoSlug, baseCommitHash, headCommitHash);
    }

    @Override
    public boolean checkFileExistsInBranch(OkHttpClient client, String workspace, String repoSlug, String branchName, String filePath) throws IOException {
        CheckFileExistsInBranchAction action = new CheckFileExistsInBranchAction(client);
        return action.fileExists(workspace, repoSlug, branchName, filePath);
    }

    @Override
    public Long findPullRequestForCommit(OkHttpClient client, String workspace, String repoSlug, String commitHash) throws IOException {
        // Bitbucket API: GET /2.0/repositories/{workspace}/{repo_slug}/commit/{commit}/pullrequests
        // Returns list of PRs that contain this commit
        String url = String.format("%s/repositories/%s/%s/commit/%s/pullrequests", 
                BITBUCKET_API_BASE, workspace, repoSlug, commitHash);
        
        Request request = new Request.Builder()
                .url(url)
                .addHeader("Accept", "application/json")
                .get()
                .build();
        
        try (Response response = client.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                log.warn("Failed to find PR for commit {}: HTTP {}", commitHash, response.code());
                return null;
            }
            
            String body = response.body() != null ? response.body().string() : "{}";
            JsonNode root = objectMapper.readTree(body);
            JsonNode values = root.get("values");
            
            if (values != null && values.isArray() && values.size() > 0) {
                // Return the first merged PR number
                for (JsonNode pr : values) {
                    String state = pr.has("state") ? pr.get("state").asText() : "";
                    if ("MERGED".equalsIgnoreCase(state)) {
                        int prId = pr.get("id").asInt();
                        log.debug("Found merged PR #{} for commit {}", prId, commitHash);
                        return (long) prId;
                    }
                }
                // If no merged PR, return the first one anyway
                int prId = values.get(0).get("id").asInt();
                log.debug("Found PR #{} for commit {} (not necessarily merged)", prId, commitHash);
                return (long) prId;
            }
            
            log.debug("No PR found for commit {}", commitHash);
            return null;
        } catch (Exception e) {
            log.warn("Error finding PR for commit {}: {}", commitHash, e.getMessage());
            return null;
        }
    }
}
