package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;

import java.util.List;
import java.util.Map;

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
    void acceptsManifestOnlyForThePinnedBaseAndHeadPair() {
        assertThat(AbstractVcsAiClientService.sameSnapshot(
                "base", "head", "base", "head")).isTrue();
        assertThat(AbstractVcsAiClientService.sameSnapshot(
                "base", "head", "base", "new-head")).isFalse();
        assertThat(AbstractVcsAiClientService.sameSnapshot(
                "base", "head", "new-base", "head")).isFalse();
        assertThat(AbstractVcsAiClientService.sameSnapshot(
                null, "head", null, "head")).isFalse();
    }

    @Test
    void emitsOneReceiptForEveryManifestPathIncludingPolicyExclusionsAndFailures() {
        PrEnrichmentDataDto partial = new PrEnrichmentDataDto(
                List.of(FileContentDto.of("src/App.java", "class App {}")),
                List.of(),
                List.of(),
                PrEnrichmentDataDto.EnrichmentStats.empty());

        PrEnrichmentDataDto result = AbstractVcsAiClientService.withPathReceipts(
                partial,
                List.of(
                        "src/App.java",
                        "generated/Api.java",
                        "vendor/Legacy.java",
                        "src/Unavailable.java"),
                Map.of(
                        "generated/Api.java", "generated",
                        "vendor/Legacy.java", "excluded"));

        assertThat(result.fileContents())
                .extracting(FileContentDto::path, FileContentDto::skipReason)
                .containsExactly(
                        org.assertj.core.groups.Tuple.tuple("src/App.java", null),
                        org.assertj.core.groups.Tuple.tuple("generated/Api.java", "generated"),
                        org.assertj.core.groups.Tuple.tuple("vendor/Legacy.java", "excluded"),
                        org.assertj.core.groups.Tuple.tuple("src/Unavailable.java", "fetch_failed"));
        assertThat(result.stats().totalFilesRequested()).isEqualTo(4);
        assertThat(result.stats().filesEnriched()).isEqualTo(1);
        assertThat(result.stats().filesSkipped()).isEqualTo(3);
    }

}
