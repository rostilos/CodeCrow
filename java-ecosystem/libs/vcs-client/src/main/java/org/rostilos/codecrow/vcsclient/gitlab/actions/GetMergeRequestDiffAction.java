package org.rostilos.codecrow.vcsclient.gitlab.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * Action to get the diff for a GitLab Merge Request.
 * Uses the /diffs endpoint (replaces deprecated /changes endpoint).
 * 
 * @see <a href="https://docs.gitlab.com/ee/api/merge_requests.html#list-merge-request-diffs">GitLab API: List merge request diffs</a>
 */
public class GetMergeRequestDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetMergeRequestDiffAction.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    private static final int DEFAULT_PER_PAGE = 100;
    private final OkHttpClient authorizedOkHttpClient;

    public GetMergeRequestDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Get the diff for a merge request using the /diffs endpoint.
     * This endpoint returns paginated results, so we fetch all pages.
     * 
     * @param namespace the project namespace (group or user)
     * @param project the project path
     * @param mergeRequestIid the merge request IID (internal ID)
     * @return the diff as a unified diff string
     */
    public String getMergeRequestDiff(String namespace, String project, int mergeRequestIid) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        
        // Use the /diffs endpoint (replaces deprecated /changes endpoint)
        // API: GET /projects/:id/merge_requests/:merge_request_iid/diffs
        List<JsonNode> allDiffs = fetchAllDiffs(encodedPath, mergeRequestIid);
        
        return buildUnifiedDiff(allDiffs);
    }

    /**
     * Fetch all diffs with pagination support.
     * The /diffs endpoint returns paginated results.
     */
    private List<JsonNode> fetchAllDiffs(String encodedPath, int mergeRequestIid) throws IOException {
        List<JsonNode> allDiffs = new ArrayList<>();
        int page = 1;
        boolean hasMore = true;
        
        while (hasMore) {
            String apiUrl = String.format("%s/projects/%s/merge_requests/%d/diffs?page=%d&per_page=%d",
                    GitLabConfig.API_BASE, encodedPath, mergeRequestIid, page, DEFAULT_PER_PAGE);

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
                
                String responseBody = resp.body() != null ? resp.body().string() : "[]";
                JsonNode diffsArray = objectMapper.readTree(responseBody);
                
                if (!diffsArray.isArray() || diffsArray.isEmpty()) {
                    hasMore = false;
                } else {
                    for (JsonNode diff : diffsArray) {
                        allDiffs.add(diff);
                    }
                    
                    // Check if there are more pages
                    String totalPages = resp.header("X-Total-Pages");
                    if (totalPages != null) {
                        hasMore = page < Integer.parseInt(totalPages);
                    } else {
                        // If no pagination headers, assume no more pages if we got less than per_page
                        hasMore = diffsArray.size() >= DEFAULT_PER_PAGE;
                    }
                    page++;
                }
            }
        }
        
        log.debug("Fetched {} diffs for MR {}", allDiffs.size(), mergeRequestIid);
        return allDiffs;
    }

    /**
     * Build a unified diff from GitLab's /diffs response.
     * The /diffs endpoint returns an array of diff objects directly.
     */
    private String buildUnifiedDiff(List<JsonNode> diffs) {
        StringBuilder combinedDiff = new StringBuilder();
        
        if (diffs.isEmpty()) {
            log.warn("No diffs found in merge request response");
            return "";
        }
        
        for (JsonNode change : diffs) {
            String oldPath = change.has("old_path") ? change.get("old_path").asText() : "";
            String newPath = change.has("new_path") ? change.get("new_path").asText() : "";
            String diff = change.has("diff") ? change.get("diff").asText() : "";
            boolean newFile = change.has("new_file") && change.get("new_file").asBoolean();
            boolean deletedFile = change.has("deleted_file") && change.get("deleted_file").asBoolean();
            boolean renamedFile = change.has("renamed_file") && change.get("renamed_file").asBoolean();
            
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
