package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CloudAnnotation;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsAnnotation;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsReport;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

import static java.lang.String.format;


//TODO: bstract or a single client class for actions
public class PostReportOnBitbucketCloudAction {
    private static final String REPORT_KEY = "org.rostilos.codecrow";
    private static final MediaType APPLICATION_JSON_MEDIA_TYPE = MediaType.get("application/json");
    private static final Logger LOGGER = LoggerFactory.getLogger(PostReportOnBitbucketCloudAction.class);
    private final VcsRepoInfo vcsRepoInfo;
    private final OkHttpClient authorizedOkHttpClient;
    private final ObjectMapper objectMapper;

    public PostReportOnBitbucketCloudAction(
            VcsRepoInfo vcsRepoInfo,
            OkHttpClient authorizedOkHttpClient
    ) {
        this.vcsRepoInfo = vcsRepoInfo;
        this.objectMapper = new ObjectMapper();
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }


    void deleteExistingReport(String commit, String workspace, String repoSlug) throws IOException {
        Request req = new Request.Builder()
                .delete()
                .url(format("https://api.bitbucket.org/2.0/repositories/%s/%s/commit/%s/reports/%s", workspace, repoSlug, commit, REPORT_KEY))
                .build();

        LOGGER.info("Deleting existing reports on bitbucket cloud");

        try (Response response = authorizedOkHttpClient.newCall(req).execute()) {
            // we dont need to validate the output here since most of the time this call will just return a 404
        }
    }

    public void uploadReport(String commit, CodeInsightsReport codeInsightReport) throws IOException {
        String workspace = vcsRepoInfo.getRepoWorkspace();
        String repoSlug = vcsRepoInfo.getRepoSlug();

        // Validate that we have proper workspace/repo values (not UUIDs with braces)
        if (workspace != null && workspace.startsWith("{") && workspace.endsWith("}")) {
            LOGGER.error("Invalid workspace format (UUID with braces): {}. Expected workspace slug.", workspace);
            throw new IOException("Invalid workspace format. VCS binding has UUID instead of workspace slug: " + workspace);
        }
        if (repoSlug != null && repoSlug.startsWith("{") && repoSlug.endsWith("}")) {
            LOGGER.error("Invalid repoSlug format (UUID with braces): {}. Expected repository slug.", repoSlug);
            throw new IOException("Invalid repository format. VCS binding has UUID instead of repo slug: " + repoSlug);
        }

        deleteExistingReport(commit, workspace, repoSlug);


        String targetUrl = format("https://api.bitbucket.org/2.0/repositories/%s/%s/commit/%s/reports/%s", workspace, repoSlug, commit, REPORT_KEY);
        String body = objectMapper.writeValueAsString(codeInsightReport);
        Request req = new Request.Builder()
                .put(RequestBody.create(body, APPLICATION_JSON_MEDIA_TYPE))
                .url(targetUrl)
                .build();

        LOGGER.info("Create report on bitbucket cloud - Target URL: {}", targetUrl);
        LOGGER.info("Request will be sent to host: {} path: {}", req.url().host(), req.url().encodedPath());
        LOGGER.info("Request body length: {} bytes", body.length());
        LOGGER.debug("Create report: {}", body);

        try (Response response = authorizedOkHttpClient.newCall(req).execute()) {
            // Log where the response actually came from
            LOGGER.info("Response received - code: {}, message: {}", response.code(), response.message());
            LOGGER.info("Response from actual URL: {}", response.request().url());
            LOGGER.info("Response protocol: {}", response.protocol());
            LOGGER.info("Response headers count: {}", response.headers().size());
            
            // Log key response headers
            String server = response.header("Server");
            String contentType = response.header("Content-Type");
            LOGGER.info("Response Server header: {}", server);
            LOGGER.info("Response Content-Type: {}", contentType);
            
            if (!response.isSuccessful()) {
                String responseBody = response.body() != null ? response.body().string() : "no body";
                LOGGER.error("Bitbucket API error - URL: {}, Status: {}", targetUrl, response.code());
                LOGGER.error("Response body (first 500 chars): {}", 
                    responseBody.length() > 500 ? responseBody.substring(0, 500) : responseBody);
                
                // Check if response body contains Bitbucket Server indicators
                if (responseBody.contains("rest/2.0/accounts") || responseBody.contains("org.glassfish.jersey")) {
                    LOGGER.error("DETECTED: Response appears to be from Bitbucket Server/Data Center, not Bitbucket Cloud!");
                    LOGGER.error("This suggests a network proxy or DNS redirect is intercepting requests to api.bitbucket.org");
                    LOGGER.error("Server header was: {}", server);
                }
                
                throw new IOException(responseBody);
            }
        }
    }

    public void uploadAnnotations(String commit, Set<CodeInsightsAnnotation> baseAnnotations) throws IOException {
        Set<CloudAnnotation> annotations = baseAnnotations.stream().map(CloudAnnotation.class::cast).collect(Collectors.toSet());

        if (annotations.isEmpty()) {
            return;
        }

        String workspace = vcsRepoInfo.getRepoWorkspace();
        String repoSlug = vcsRepoInfo.getRepoSlug();
        Request req = new Request.Builder()
                .post(RequestBody.create(objectMapper.writeValueAsString(annotations), APPLICATION_JSON_MEDIA_TYPE))
                .url(format("https://api.bitbucket.org/2.0/repositories/%s/%s/commit/%s/reports/%s/annotations", workspace, repoSlug, commit, REPORT_KEY))
                .build();

        LOGGER.info("Creating annotations on bitbucket cloud");
        LOGGER.atDebug().setMessage("Create annotations: {}").addArgument(() -> {
            try {
                return objectMapper.writeValueAsString(annotations);
            } catch (JsonProcessingException e) {
                return "An error occurred whilst converting annotations to JSON: " + e.getClass().getName() + ": " + e.getMessage();
            }
        }).log();


        try (Response response = authorizedOkHttpClient.newCall(req).execute()) {
            validate(response);
        }
    }

    private String validate(Response response) throws IOException {
        if (!response.isSuccessful()) {
            String error = Optional.ofNullable(response.body()).map(b -> {
                try {
                    return b.string();
                } catch (IOException e) {
                    throw new IllegalStateException("Could not retrieve response content", e);
                }
            }).orElse("Request failed but Bitbucket didn't respond with a proper error message");
            throw new IOException(error);
        } else {
            assert response.body() != null;
            return response.body().string();
        }
    }
}
