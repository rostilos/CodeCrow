package org.rostilos.codecrow.pipelineagent.github.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.vcsclient.github.actions.CheckFileExistsInBranchAction;
import org.rostilos.codecrow.vcsclient.github.actions.GetCommitDiffAction;
import org.rostilos.codecrow.vcsclient.github.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestDiffAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;

/**
 * GitHub implementation of VcsOperationsService.
 * Delegates to GitHub-specific action classes for API calls.
 */
@Service
public class GitHubOperationsService implements VcsOperationsService {

    private static final Logger log = LoggerFactory.getLogger(GitHubOperationsService.class);
    private static final String GITHUB_API_BASE = "https://api.github.com";
    private static final ObjectMapper objectMapper = new ObjectMapper();

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITHUB;
    }

    @Override
    public String getCommitDiff(OkHttpClient client, String owner, String repoSlug, String commitHash) throws IOException {
        GetCommitDiffAction action = new GetCommitDiffAction(client);
        return action.getCommitDiff(owner, repoSlug, commitHash);
    }

    @Override
    public String getPullRequestDiff(OkHttpClient client, String owner, String repoSlug, String prNumber) throws IOException {
        GetPullRequestDiffAction action = new GetPullRequestDiffAction(client);
        return action.getPullRequestDiff(owner, repoSlug, Integer.parseInt(prNumber));
    }

    @Override
    public String getCommitRangeDiff(OkHttpClient client, String owner, String repoSlug, String baseCommitHash, String headCommitHash) throws IOException {
        GetCommitRangeDiffAction action = new GetCommitRangeDiffAction(client);
        return action.getCommitRangeDiff(owner, repoSlug, baseCommitHash, headCommitHash);
    }

    @Override
    public boolean checkFileExistsInBranch(OkHttpClient client, String owner, String repoSlug, String branchName, String filePath) throws IOException {
        CheckFileExistsInBranchAction action = new CheckFileExistsInBranchAction(client);
        return action.fileExists(owner, repoSlug, branchName, filePath);
    }

    @Override
    public Long findPullRequestForCommit(OkHttpClient client, String owner, String repoSlug, String commitHash) throws IOException {
        // GitHub API: GET /repos/{owner}/{repo}/commits/{commit_sha}/pulls
        // Returns list of PRs associated with this commit
        String url = String.format("%s/repos/%s/%s/commits/%s/pulls", 
                GITHUB_API_BASE, owner, repoSlug, commitHash);
        
        Request request = new Request.Builder()
                .url(url)
                .addHeader("Accept", "application/vnd.github.v3+json")
                .get()
                .build();
        
        try (Response response = client.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                log.warn("Failed to find PR for commit {}: HTTP {}", commitHash, response.code());
                return null;
            }
            
            String body = response.body() != null ? response.body().string() : "[]";
            JsonNode pullRequests = objectMapper.readTree(body);
            
            if (pullRequests.isArray() && pullRequests.size() > 0) {
                // Return the first (most recent) merged PR number
                for (JsonNode pr : pullRequests) {
                    // Check if merged
                    if (pr.has("merged_at") && !pr.get("merged_at").isNull()) {
                        int prNumber = pr.get("number").asInt();
                        log.debug("Found merged PR #{} for commit {}", prNumber, commitHash);
                        return (long) prNumber;
                    }
                }
                // If no merged PR, return the first one anyway
                int prNumber = pullRequests.get(0).get("number").asInt();
                log.debug("Found PR #{} for commit {} (not necessarily merged)", prNumber, commitHash);
                return (long) prNumber;
            }
            
            log.debug("No PR found for commit {}", commitHash);
            return null;
        } catch (Exception e) {
            log.warn("Error finding PR for commit {}: {}", commitHash, e.getMessage());
            return null;
        }
    }
}
