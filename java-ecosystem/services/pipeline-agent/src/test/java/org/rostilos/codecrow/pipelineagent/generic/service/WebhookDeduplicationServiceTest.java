package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class WebhookDeduplicationServiceTest {

    @Test
    void mergeEventWithPrContextWinsWhenPushArrivesFirst() {
        WebhookDeduplicationService service = new WebhookDeduplicationService();

        assertThat(service.isDuplicateBranchEvent(1L, "main", "push")).isFalse();
        assertThat(service.isDuplicateBranchEvent(1L, "main", "pull_request")).isFalse();
    }

    @Test
    void pushIsSuppressedWhenMergeEventWasAlreadyAccepted() {
        WebhookDeduplicationService service = new WebhookDeduplicationService();

        assertThat(service.isDuplicateBranchEvent(1L, "main", "pullrequest:fulfilled")).isFalse();
        assertThat(service.isDuplicateBranchEvent(1L, "main", "repo:push")).isTrue();
    }
}
