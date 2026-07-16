package org.rostilos.codecrow.analysisengine.execution;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ExecutionInputArtifactBundleTest {
    private static final String EXECUTION_ID = "execution-pr-42-v1";
    private static final String HEAD_SHA = "b".repeat(40);
    private static final String ARTIFACT_SCHEMA = "review-artifact-v1";
    private static final String PRODUCER = "java-vcs-acquisition";
    private static final String PRODUCER_VERSION = "analysis-engine-v1";

    @Test
    void createsCanonicalExactUtf8ArtifactsForDiffSourceAndEnrichment() throws Exception {
        byte[] rawDiff = "+print('π')\n".getBytes(StandardCharsets.UTF_8);
        String source = "π\n";
        PrEnrichmentDataDto enrichment = enrichment(
                List.of(FileContentDto.of("src/π.py", source)),
                source.getBytes(StandardCharsets.UTF_8).length);

        ExecutionInputArtifactBundle bundle = ExecutionInputArtifactBundle.create(
                EXECUTION_ID,
                HEAD_SHA,
                "diff:fixture-v1",
                rawDiff,
                enrichment,
                ARTIFACT_SCHEMA,
                PRODUCER,
                PRODUCER_VERSION);

        assertThat(bundle.entries())
                .isSortedAccordingTo(java.util.Comparator.comparing(
                        ArtifactManifestEntry::artifactId));
        assertThat(bundle.artifacts()).hasSize(3);
        ExecutionArtifactPayload sourceArtifact = artifact(
                bundle, ArtifactManifestEntry.Kind.SOURCE_FILE);
        assertThat(sourceArtifact.entry().artifactId()).isEqualTo(
                "source:" + sha256((EXECUTION_ID + "\0src/π.py")
                        .getBytes(StandardCharsets.UTF_8)));
        assertThat(sourceArtifact.entry().contentKey()).isEqualTo("src/π.py");
        assertThat(sourceArtifact.content()).isEqualTo(source.getBytes(StandardCharsets.UTF_8));

        ExecutionArtifactPayload enrichmentArtifact = artifact(
                bundle, ArtifactManifestEntry.Kind.PR_ENRICHMENT);
        assertThat(enrichmentArtifact.entry().artifactId()).isEqualTo(
                "enrichment:" + sha256((EXECUTION_ID + "\0pr-enrichment.json")
                        .getBytes(StandardCharsets.UTF_8)));
        assertThat(new String(enrichmentArtifact.content(), StandardCharsets.UTF_8))
                .isEqualTo("{\"fileContents\":[{\"content\":\"π\\n\","
                        + "\"path\":\"src/π.py\",\"sizeBytes\":3,"
                        + "\"skipReason\":null,\"skipped\":false}],"
                        + "\"fileMetadata\":[],\"relationships\":[],"
                        + "\"stats\":{\"filesEnriched\":1,\"filesSkipped\":0,"
                        + "\"processingTimeMs\":7,\"relationshipsFound\":0,"
                        + "\"skipReasons\":{},\"totalContentSizeBytes\":3,"
                        + "\"totalFilesRequested\":1}}");
        assertThat(new ObjectMapper().writeValueAsString(enrichment))
                .contains("\"totalContentSize\":3");
    }

    @Test
    void canonicalInputDigestBindsDiffSourcePathsAndExplicitGapsWithoutExecutionId() {
        byte[] rawDiff = "diff-v1".getBytes(StandardCharsets.UTF_8);
        PrEnrichmentDataDto baselineInputs = enrichmentWithGap(
                "src/A.java", "class A {}", "binary-file");

        String baseline = ExecutionInputArtifactBundle.canonicalInputDigest(
                rawDiff, baselineInputs);
        String exactReplay = ExecutionInputArtifactBundle.canonicalInputDigest(
                rawDiff.clone(), enrichmentWithGap(
                        "src/A.java", "class A {}", "binary-file"));
        String changedDiff = ExecutionInputArtifactBundle.canonicalInputDigest(
                "diff-v2".getBytes(StandardCharsets.UTF_8), baselineInputs);
        String changedSource = ExecutionInputArtifactBundle.canonicalInputDigest(
                rawDiff, enrichmentWithGap(
                        "src/A.java", "class A { int value; }", "binary-file"));
        String changedPath = ExecutionInputArtifactBundle.canonicalInputDigest(
                rawDiff, enrichmentWithGap(
                        "src/RenamedA.java", "class A {}", "binary-file"));
        String changedGap = ExecutionInputArtifactBundle.canonicalInputDigest(
                rawDiff, enrichmentWithGap(
                        "src/A.java", "class A {}", "file-too-large"));
        String explicitEmptyEnrichment = ExecutionInputArtifactBundle.canonicalInputDigest(
                rawDiff, PrEnrichmentDataDto.empty());
        String absentEnrichment = ExecutionInputArtifactBundle.canonicalInputDigest(
                rawDiff, null);

        assertThat(baseline).matches("[0-9a-f]{64}").isEqualTo(exactReplay);
        assertThat(List.of(
                baseline,
                changedDiff,
                changedSource,
                changedPath,
                changedGap,
                explicitEmptyEnrichment,
                absentEnrichment)).doesNotHaveDuplicates();
    }

    @Test
    void rejectsDuplicateMissingSkippedOrIncorrectlySizedSourceInputs() {
        FileContentDto valid = FileContentDto.of("src/A.java", "class A {}");

        assertThatThrownBy(() -> bundle(enrichment(
                List.of(valid, valid), valid.sizeBytes() * 2)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("duplicate source path");
        assertThatThrownBy(() -> bundle(enrichment(
                List.of(new FileContentDto("src/A.java", null, 0, false, null)), 0)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("must carry content");
        assertThatThrownBy(() -> bundle(enrichment(
                List.of(new FileContentDto("src/A.java", "unexpected", 0, true, "gap")), 0)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("cannot carry content");
        assertThatThrownBy(() -> bundle(new PrEnrichmentDataDto(
                List.of(FileContentDto.skipped("src/A.java", "   ")),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        1, 0, 1, 0, 0, 7, Map.of()))))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("explicit reason");
        assertThatThrownBy(() -> bundle(enrichment(
                List.of(new FileContentDto("src/A.java", "π", 1, false, null)), 1)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("UTF-8 exact");
        assertThatThrownBy(() -> bundle(new PrEnrichmentDataDto(
                List.of(valid),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        2, 1, 1, 0, valid.sizeBytes(), 7, Map.of()))))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("accounting");
    }

    @Test
    void artifactPayloadDefensivelyCopiesInputAndReturnedBytes() {
        byte[] content = "immutable".getBytes(StandardCharsets.UTF_8);
        ArtifactManifestEntry entry = new ArtifactManifestEntry(
                EXECUTION_ID,
                "source:immutable",
                "src/Immutable.java",
                HEAD_SHA,
                sha256(content),
                content.length,
                ArtifactManifestEntry.Kind.SOURCE_FILE,
                ARTIFACT_SCHEMA,
                PRODUCER,
                PRODUCER_VERSION);
        ExecutionArtifactPayload payload = new ExecutionArtifactPayload(entry, content);

        content[0] = 'X';
        byte[] returned = payload.content();
        returned[0] = 'Y';

        assertThat(new String(payload.content(), StandardCharsets.UTF_8))
                .isEqualTo("immutable");
        assertThat(payload).isEqualTo(new ExecutionArtifactPayload(
                entry, "immutable".getBytes(StandardCharsets.UTF_8)));
        assertThat(payload.hashCode()).isEqualTo(new ExecutionArtifactPayload(
                entry, "immutable".getBytes(StandardCharsets.UTF_8)).hashCode());
    }

    private static ExecutionInputArtifactBundle bundle(PrEnrichmentDataDto enrichment) {
        return ExecutionInputArtifactBundle.create(
                EXECUTION_ID,
                HEAD_SHA,
                "diff:fixture-v1",
                "diff".getBytes(StandardCharsets.UTF_8),
                enrichment,
                ARTIFACT_SCHEMA,
                PRODUCER,
                PRODUCER_VERSION);
    }

    private static PrEnrichmentDataDto enrichment(
            List<FileContentDto> contents,
            long totalBytes) {
        return new PrEnrichmentDataDto(
                contents,
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        contents.size(),
                        contents.size(),
                        0,
                        0,
                        totalBytes,
                        7,
                        Map.of()));
    }

    private static PrEnrichmentDataDto enrichmentWithGap(
            String sourcePath,
            String source,
            String gapReason) {
        FileContentDto sourceFile = FileContentDto.of(sourcePath, source);
        return new PrEnrichmentDataDto(
                List.of(
                        sourceFile,
                        FileContentDto.skipped("assets/image.bin", gapReason)),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        2,
                        1,
                        1,
                        0,
                        sourceFile.sizeBytes(),
                        0,
                        Map.of(gapReason, 1)));
    }

    private static ExecutionArtifactPayload artifact(
            ExecutionInputArtifactBundle bundle,
            ArtifactManifestEntry.Kind kind) {
        return bundle.artifacts().stream()
                .filter(item -> item.entry().kind() == kind)
                .findFirst()
                .orElseThrow();
    }

    private static String sha256(byte[] value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new AssertionError(error);
        }
    }
}
