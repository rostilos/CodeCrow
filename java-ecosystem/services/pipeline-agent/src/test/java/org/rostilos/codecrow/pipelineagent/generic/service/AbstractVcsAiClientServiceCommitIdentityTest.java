package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class AbstractVcsAiClientServiceCommitIdentityTest {
    private static final String SHA1 =
            "eb59a730e56532cc96d0e9fbb6b7616d6ca9897e";
    private static final String SHA256 =
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    @Test
    void acceptsOnlyFullProviderObjectIds() {
        assertThat(AbstractVcsAiClientService.isFullGitObjectId(SHA1)).isTrue();
        assertThat(AbstractVcsAiClientService.isFullGitObjectId(SHA256)).isTrue();
        assertThat(AbstractVcsAiClientService.isFullGitObjectId("eb59a730e565")).isFalse();
        assertThat(AbstractVcsAiClientService.isFullGitObjectId("not-a-commit")).isFalse();
        assertThat(AbstractVcsAiClientService.isFullGitObjectId(null)).isFalse();
    }

}
