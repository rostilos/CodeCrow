package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Optional;

/**
 * Action to retrieve pull request metadata from Bitbucket Cloud.
 */
public class GetPullRequestAction {

    private static final Logger log = LoggerFactory.getLogger(GetPullRequestAction.class);
    private final OkHttpClient authorizedOkHttpClient;
    private final ObjectMapper objectMapper;

    public GetPullRequestAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
        this.objectMapper = new ObjectMapper();
    }

    /**
     * Represents PR metadata.
     */
    public static class PullRequestMetadata {
        private final String title;
        private final String description;
        private final String state;
        private final String sourceRef;
        private final String destRef;

        public PullRequestMetadata(String title, String description, String state, String sourceRef, String destRef) {
            this.title = title;
            this.description = description;
            this.state = state;
            this.sourceRef = sourceRef;
            this.destRef = destRef;
        }

        public String getTitle() { return title; }
        public String getDescription() { return description; }
        public String getState() { return state; }
        public String getSourceRef() { return sourceRef; }
        public String getDestRef() { return destRef; }
    }

    /**
     * Fetches pull request metadata.
     *
     * @param workspace workspace or team slug
     * @param repoSlug repository slug
     * @param prNumber pull request id
     * @return PullRequestMetadata
     * @throws IOException on network / parsing errors
     */
    public PullRequestMetadata getPullRequest(String workspace, String repoSlug, String prNumber) throws IOException {
        String ws = Optional.ofNullable(workspace).orElse("");
        String apiUrl = String.format("%s/repositories/%s/%s/pullrequests/%s",
                BitbucketCloudConfig.BITBUCKET_API_BASE, ws, repoSlug, prNumber);

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

            String responseBody = resp.body() != null ? resp.body().string() : "{}";
            JsonNode json = objectMapper.readTree(responseBody);

            String title = json.has("title") ? json.get("title").asText() : "";
            String description = json.has("description") ? json.get("description").asText() : "";
            String state = json.has("state") ? json.get("state").asText() : "";

            String sourceRef = "";
            String destRef = "";

            if (json.has("source") && json.get("source").has("branch")) {
                sourceRef = json.get("source").get("branch").get("name").asText();
            }

            if (json.has("destination") && json.get("destination").has("branch")) {
                destRef = json.get("destination").get("branch").get("name").asText();
            }

            return new PullRequestMetadata(title, description, state, sourceRef, destRef);

        } catch (IOException e) {
            log.error("Failed to get pull request: {}", e.getMessage(), e);
            throw e;
        }
    }
}

