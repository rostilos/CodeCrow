package org.rostilos.codecrow.pipelineagent.gitlab.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.vcsclient.gitlab.actions.CheckFileExistsInBranchAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetCommitDiffAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetCommitRangeDiffAction;
import org.rostilos.codecrow.vcsclient.gitlab.actions.GetMergeRequestDiffAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * GitLab implementation of VcsOperationsService.
 * Delegates to GitLab-specific action classes for API calls.
 */
@Service
public class GitLabOperationsService implements VcsOperationsService {

    private static final Logger log = LoggerFactory.getLogger(GitLabOperationsService.class);
    private static final String GITLAB_API_BASE = "https://gitlab.com/api/v4";
    private static final ObjectMapper objectMapper = new ObjectMapper();

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITLAB;
    }

    @Override
    public String getCommitDiff(OkHttpClient client, String namespace, String project, String commitHash) throws IOException {
        GetCommitDiffAction action = new GetCommitDiffAction(client);
        return action.getCommitDiff(namespace, project, commitHash);
    }

    @Override
    public String getPullRequestDiff(OkHttpClient client, String namespace, String project, String mergeRequestIid) throws IOException {
        GetMergeRequestDiffAction action = new GetMergeRequestDiffAction(client);
        return action.getMergeRequestDiff(namespace, project, Integer.parseInt(mergeRequestIid));
    }

    @Override
    public String getCommitRangeDiff(OkHttpClient client, String namespace, String project, String baseCommitHash, String headCommitHash) throws IOException {
        GetCommitRangeDiffAction action = new GetCommitRangeDiffAction(client);
        return action.getCommitRangeDiff(namespace, project, baseCommitHash, headCommitHash);
    }

    @Override
    public boolean checkFileExistsInBranch(OkHttpClient client, String namespace, String project, String branchName, String filePath) throws IOException {
        CheckFileExistsInBranchAction action = new CheckFileExistsInBranchAction(client);
        return action.fileExists(namespace, project, branchName, filePath);
    }

    @Override
    public Long findPullRequestForCommit(OkHttpClient client, String namespace, String project, String commitHash) throws IOException {
        // GitLab API: GET /api/v4/projects/{id}/repository/commits/{sha}/merge_requests
        // The project path needs to be URL-encoded
        String projectPath = namespace + "/" + project;
        String encodedProjectPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        
        String url = String.format("%s/projects/%s/repository/commits/%s/merge_requests", 
                GITLAB_API_BASE, encodedProjectPath, commitHash);
        
        Request request = new Request.Builder()
                .url(url)
                .addHeader("Accept", "application/json")
                .get()
                .build();
        
        try (Response response = client.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                log.warn("Failed to find MR for commit {}: HTTP {}", commitHash, response.code());
                return null;
            }
            
            String body = response.body() != null ? response.body().string() : "[]";
            JsonNode mergeRequests = objectMapper.readTree(body);
            
            if (mergeRequests.isArray() && mergeRequests.size() > 0) {
                // Return the first merged MR iid
                for (JsonNode mr : mergeRequests) {
                    String state = mr.has("state") ? mr.get("state").asText() : "";
                    if ("merged".equalsIgnoreCase(state)) {
                        int iid = mr.get("iid").asInt();
                        log.debug("Found merged MR !{} for commit {}", iid, commitHash);
                        return (long) iid;
                    }
                }
                // If no merged MR, return the first one anyway
                int iid = mergeRequests.get(0).get("iid").asInt();
                log.debug("Found MR !{} for commit {} (not necessarily merged)", iid, commitHash);
                return (long) iid;
            }
            
            log.debug("No MR found for commit {}", commitHash);
            return null;
        } catch (Exception e) {
            log.warn("Error finding MR for commit {}: {}", commitHash, e.getMessage());
            return null;
        }
    }
}
