package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
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
    private final ProjectVcsConnectionBinding projectVcsConnectionBinding;
    private final OkHttpClient authorizedOkHttpClient;
    private final ObjectMapper objectMapper;

    public PostReportOnBitbucketCloudAction(
            ProjectVcsConnectionBinding projectVcsConnectionBinding,
            OkHttpClient authorizedOkHttpClient
    ) {
        this.projectVcsConnectionBinding = projectVcsConnectionBinding;
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
        String workspace = projectVcsConnectionBinding.getWorkspace();
        String repoSlug = projectVcsConnectionBinding.getRepoSlug();

        deleteExistingReport(commit, workspace, repoSlug);


        String targetUrl = format("https://api.bitbucket.org/2.0/repositories/%s/%s/commit/%s/reports/%s", workspace, repoSlug, commit, REPORT_KEY);
        String body = objectMapper.writeValueAsString(codeInsightReport);
        Request req = new Request.Builder()
                .put(RequestBody.create(body, APPLICATION_JSON_MEDIA_TYPE))
                .url(targetUrl)
                .build();

        LOGGER.info("Create report on bitbucket cloud: {}", targetUrl);
        LOGGER.debug("Create report: {}", body);

        try (Response response = authorizedOkHttpClient.newCall(req).execute()) {
            validate(response);
        }
    }

    public void uploadAnnotations(String commit, Set<CodeInsightsAnnotation> baseAnnotations) throws IOException {
        Set<CloudAnnotation> annotations = baseAnnotations.stream().map(CloudAnnotation.class::cast).collect(Collectors.toSet());

        if (annotations.isEmpty()) {
            return;
        }

        String workspace = projectVcsConnectionBinding.getWorkspace();
        String repoSlug = projectVcsConnectionBinding.getRepoSlug();
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
