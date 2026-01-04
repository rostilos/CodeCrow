package org.rostilos.codecrow.vcsclient.gitlab.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * Action to compare two commits in GitLab and get the diff.
 */
public class GetCommitRangeDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetCommitRangeDiffAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public GetCommitRangeDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Get the diff between two commits.
     * 
     * @param namespace the project namespace (group or user)
     * @param project the project path
     * @param baseCommitSha the base commit SHA
     * @param headCommitSha the head commit SHA
     * @return the diff as a unified diff string
     */
    public String getCommitRangeDiff(String namespace, String project, String baseCommitSha, String headCommitSha) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        
        // GitLab uses compare endpoint for commit range diff
        String apiUrl = String.format("%s/projects/%s/repository/compare?from=%s&to=%s",
                GitLabConfig.API_BASE, encodedPath, 
                URLEncoder.encode(baseCommitSha, StandardCharsets.UTF_8),
                URLEncoder.encode(headCommitSha, StandardCharsets.UTF_8));

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("GitLab returned non-success response %d for URL %s: %s",
                        resp.code(), apiUrl, body);
                log.warn(msg);
                throw new IOException(msg);
            }
            
            String responseBody = resp.body() != null ? resp.body().string() : "{}";
            return buildUnifiedDiff(responseBody);
        }
    }

    /**
     * Build a unified diff from GitLab's compare response.
     */
    private String buildUnifiedDiff(String responseBody) throws IOException {
        StringBuilder combinedDiff = new StringBuilder();
        com.fasterxml.jackson.databind.ObjectMapper objectMapper = new com.fasterxml.jackson.databind.ObjectMapper();
        com.fasterxml.jackson.databind.JsonNode root = objectMapper.readTree(responseBody);
        com.fasterxml.jackson.databind.JsonNode diffs = root.get("diffs");
        
        if (diffs == null || !diffs.isArray()) {
            log.warn("No diffs found in compare response");
            return "";
        }
        
        for (com.fasterxml.jackson.databind.JsonNode diffEntry : diffs) {
            String oldPath = diffEntry.has("old_path") ? diffEntry.get("old_path").asText() : "";
            String newPath = diffEntry.has("new_path") ? diffEntry.get("new_path").asText() : "";
            String diff = diffEntry.has("diff") ? diffEntry.get("diff").asText() : "";
            boolean newFile = diffEntry.has("new_file") && diffEntry.get("new_file").asBoolean();
            boolean deletedFile = diffEntry.has("deleted_file") && diffEntry.get("deleted_file").asBoolean();
            boolean renamedFile = diffEntry.has("renamed_file") && diffEntry.get("renamed_file").asBoolean();
            
            // Build unified diff header
            String fromFile = renamedFile ? oldPath : newPath;
            combinedDiff.append("diff --git a/").append(fromFile).append(" b/").append(newPath).append("\n");
            
            if (newFile) {
                combinedDiff.append("new file mode 100644\n");
                combinedDiff.append("--- /dev/null\n");
                combinedDiff.append("+++ b/").append(newPath).append("\n");
            } else if (deletedFile) {
                combinedDiff.append("deleted file mode 100644\n");
                combinedDiff.append("--- a/").append(oldPath).append("\n");
                combinedDiff.append("+++ /dev/null\n");
            } else if (renamedFile) {
                combinedDiff.append("rename from ").append(oldPath).append("\n");
                combinedDiff.append("rename to ").append(newPath).append("\n");
                combinedDiff.append("--- a/").append(oldPath).append("\n");
                combinedDiff.append("+++ b/").append(newPath).append("\n");
            } else {
                combinedDiff.append("--- a/").append(oldPath).append("\n");
                combinedDiff.append("+++ b/").append(newPath).append("\n");
            }
            
            // Append the actual diff content
            if (!diff.isEmpty()) {
                combinedDiff.append(diff);
                if (!diff.endsWith("\n")) {
                    combinedDiff.append("\n");
                }
            }
            
            combinedDiff.append("\n");
        }
        
        return combinedDiff.toString();
    }
}
