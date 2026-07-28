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

    @Test
    void acceptsExactOrProviderStyleTwelveCharacterPrefix() {
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeCommit(SHA1, SHA1)).isTrue();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeCommit("eb59a730e565", SHA1)).isTrue();
    }

    @Test
    void acceptsMissingWebhookIdentityWhenProviderSuppliesTheAuthoritativeHead() {
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeCommit(null, SHA1)).isTrue();
    }

    @Test
    void rejectsWeakMismatchedOrMalformedIdentity() {
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeCommit("eb59a730e56", SHA1)).isFalse();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeCommit("eb59a730e566", SHA1)).isFalse();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeCommit("", SHA1)).isFalse();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeCommit(" ", SHA1)).isFalse();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeCommit(SHA1, "eb59a730e565")).isFalse();
    }

    @Test
    void acceptsOnlyTheExactNonBlankProviderBranchIdentity() {
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeBranch(
                "main", "main")).isTrue();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeBranch(
                "feature/review", "feature/review")).isTrue();

        assertThat(AbstractVcsAiClientService.matchesAuthoritativeBranch(
                "main", "develop")).isFalse();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeBranch(
                "Main", "main")).isFalse();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeBranch(
                "", "main")).isFalse();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeBranch(
                "main", " ")).isFalse();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeBranch(
                null, "main")).isFalse();
        assertThat(AbstractVcsAiClientService.matchesAuthoritativeBranch(
                "main", null)).isFalse();
    }
}
