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

/**
 * Action to get GitLab Merge Request metadata.
 */
public class GetMergeRequestAction {

    private static final Logger log = LoggerFactory.getLogger(GetMergeRequestAction.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    private final OkHttpClient authorizedOkHttpClient;

    public GetMergeRequestAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Get merge request metadata.
     * 
     * @param namespace the project namespace (group or user)
     * @param project the project path
     * @param mergeRequestIid the merge request IID (internal ID)
     * @return JsonNode containing MR metadata (title, description, source_branch, target_branch, etc.)
     */
    public JsonNode getMergeRequest(String namespace, String project, int mergeRequestIid) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        
        String apiUrl = String.format("%s/projects/%s/merge_requests/%d",
                GitLabConfig.API_BASE, encodedPath, mergeRequestIid);

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
            return objectMapper.readTree(responseBody);
        }
    }
}
