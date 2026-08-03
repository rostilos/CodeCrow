package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;

class VcsFileRetrievalPolicyTest {

    @Test
    void usesPerFileRetrievalUpToThresholdAndArchiveAboveIt() {
        VcsFileRetrievalPolicy policy = new VcsFileRetrievalPolicy(25);

        assertThat(policy.shouldUseArchive(25)).isFalse();
        assertThat(policy.allowPerFileFallback(25)).isTrue();
        assertThat(policy.shouldUseArchive(26)).isTrue();
        assertThat(policy.allowPerFileFallback(26)).isFalse();
    }

    @Test
    void clampsNegativeThresholdToArchiveOnly() {
        VcsFileRetrievalPolicy policy = new VcsFileRetrievalPolicy(-10);

        assertThat(policy.archiveFileThreshold()).isZero();
        assertThat(policy.shouldUseArchive(1)).isTrue();
    }

    @Test
    void recognizesWrappedProviderRateLimits() {
        VcsFileRetrievalPolicy policy = new VcsFileRetrievalPolicy(25);
        IOException failure = new IOException(
                "file retrieval failed",
                new IOException("Unexpected response 429: Too Many Requests"));

        assertThat(policy.isRateLimited(failure)).isTrue();
        assertThat(policy.isRateLimited(new IOException("connection reset"))).isFalse();
    }
}
