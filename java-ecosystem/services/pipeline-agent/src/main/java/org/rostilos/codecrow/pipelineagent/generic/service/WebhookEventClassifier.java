package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;

/** Provider-neutral classification for webhook workflow routing. */
public final class WebhookEventClassifier {

    private static final String BITBUCKET_MERGE_EVENT = "pullrequest:fulfilled";
    private static final String GITHUB_PR_EVENT = "pull_request";
    private static final String GITLAB_MR_EVENT = "merge_request";

    private WebhookEventClassifier() {
    }

    public static boolean isPullRequestMerge(WebhookPayload payload) {
        if (payload == null || payload.eventType() == null) {
            return false;
        }

        if (BITBUCKET_MERGE_EVENT.equals(payload.eventType())) {
            return true;
        }

        if (payload.rawPayload() == null) {
            return false;
        }

        if (GITHUB_PR_EVENT.equals(payload.eventType())) {
            return "closed".equals(payload.rawPayload().path("action").asText(""))
                    && payload.rawPayload().path("pull_request").path("merged").asBoolean(false);
        }

        if (GITLAB_MR_EVENT.equals(payload.eventType())) {
            String action = payload.rawPayload().path("object_attributes").path("action").asText("");
            String state = payload.rawPayload().path("object_attributes").path("state").asText("");
            return "merge".equals(action) || "merged".equals(state);
        }

        return false;
    }

    public static boolean isBranchAnalysisEvent(WebhookPayload payload) {
        return payload != null
                && (isPullRequestMerge(payload) || payload.isPushEvent());
    }

    /**
     * Safe only for events already classified as branch-analysis events, where
     * generic GitHub/GitLab PR event names necessarily represent a merge.
     */
    public static boolean isPullRequestEventType(String eventType) {
        return BITBUCKET_MERGE_EVENT.equals(eventType)
                || GITHUB_PR_EVENT.equals(eventType)
                || GITLAB_MR_EVENT.equals(eventType);
    }
}
