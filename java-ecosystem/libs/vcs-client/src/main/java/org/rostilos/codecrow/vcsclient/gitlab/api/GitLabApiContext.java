package org.rostilos.codecrow.vcsclient.gitlab.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabException;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Objects;

/**
 * Shared transport and endpoint context for one configured GitLab instance.
 */
public final class GitLabApiContext {

    private static final MediaType JSON = MediaType.parse("application/json");

    private final OkHttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final String apiBaseUrl;

    public GitLabApiContext(OkHttpClient httpClient, String instanceBaseUrl) {
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
        this.objectMapper = new ObjectMapper();
        this.apiBaseUrl = GitLabConfig.apiBaseUrl(instanceBaseUrl);
    }

    public ObjectMapper objectMapper() {
        return objectMapper;
    }

    public String apiBaseUrl() {
        return apiBaseUrl;
    }

    public String projectUrl(String namespace, String project) {
        return apiBaseUrl + "/projects/" + encodedProjectPath(namespace, project);
    }

    private String encodedProjectPath(String namespace, String project) {
        return encode(namespace + "/" + project);
    }

    public String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8)
                .replace("+", "%20");
    }

    public Request get(String url) {
        return request(url).get().build();
    }

    public Request head(String url) {
        return request(url).head().build();
    }

    public Request postJson(String url, String jsonBody) {
        return request(url)
                .post(RequestBody.create(jsonBody, JSON))
                .build();
    }

    public Request putJson(String url, String jsonBody) {
        return request(url)
                .put(RequestBody.create(jsonBody, JSON))
                .build();
    }

    public Request delete(String url) {
        return request(url).delete().build();
    }

    public Response execute(Request request) throws IOException {
        return httpClient.newCall(request).execute();
    }

    JsonNode executeJson(String operation, Request request) throws IOException {
        try (Response response = execute(request)) {
            if (!response.isSuccessful()) {
                throw error(operation, response);
            }
            return objectMapper.readTree(bodyOr(response, "{}"));
        }
    }

    void executeSuccessfully(String operation, Request request) throws IOException {
        try (Response response = execute(request)) {
            if (!response.isSuccessful()) {
                throw error(operation, response);
            }
        }
    }

    public IOException error(String operation, Response response) throws IOException {
        String body = bodyOr(response, "");
        GitLabException cause = new GitLabException(operation, response.code(), body);
        return new IOException(cause.getMessage(), cause);
    }

    public String bodyOr(Response response, String fallback) throws IOException {
        return response.body() != null ? response.body().string() : fallback;
    }

    private Request.Builder request(String url) {
        return new Request.Builder()
                .url(url)
                .header("Accept", "application/json");
    }
}
