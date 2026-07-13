package org.rostilos.codecrow.pipelineagent.generic.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;

import static org.assertj.core.api.Assertions.assertThat;

class WebhookEventClassifierTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void recognizesMergeEventsAcrossEverySupportedProvider() throws Exception {
        WebhookPayload bitbucket = payload(
                EVcsProvider.BITBUCKET_CLOUD, "pullrequest:fulfilled", "{}");
        WebhookPayload github = payload(
                EVcsProvider.GITHUB, "pull_request",
                "{\"action\":\"closed\",\"pull_request\":{\"merged\":true}}");
        WebhookPayload gitlab = payload(
                EVcsProvider.GITLAB, "merge_request",
                "{\"object_attributes\":{\"action\":\"merge\",\"state\":\"merged\"}}");

        assertThat(WebhookEventClassifier.isPullRequestMerge(bitbucket)).isTrue();
        assertThat(WebhookEventClassifier.isPullRequestMerge(github)).isTrue();
        assertThat(WebhookEventClassifier.isPullRequestMerge(gitlab)).isTrue();
        assertThat(WebhookEventClassifier.isBranchAnalysisEvent(gitlab)).isTrue();
    }

    @Test
    void doesNotTreatOrdinaryPrUpdatesAsMerges() throws Exception {
        WebhookPayload githubUpdate = payload(
                EVcsProvider.GITHUB, "pull_request",
                "{\"action\":\"synchronize\",\"pull_request\":{\"merged\":false}}");
        WebhookPayload gitlabUpdate = payload(
                EVcsProvider.GITLAB, "merge_request",
                "{\"object_attributes\":{\"action\":\"update\",\"state\":\"opened\"}}");

        assertThat(WebhookEventClassifier.isPullRequestMerge(githubUpdate)).isFalse();
        assertThat(WebhookEventClassifier.isPullRequestMerge(gitlabUpdate)).isFalse();
    }

    private WebhookPayload payload(
            EVcsProvider provider,
            String eventType,
            String rawJson) throws Exception {
        return new WebhookPayload(
                provider, eventType, "repo-id", "repo", "workspace", "41",
                "feature", "main", "commit", objectMapper.readTree(rawJson));
    }
}
