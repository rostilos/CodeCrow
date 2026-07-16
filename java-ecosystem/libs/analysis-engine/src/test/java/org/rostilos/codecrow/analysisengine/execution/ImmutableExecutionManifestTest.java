package org.rostilos.codecrow.analysisengine.execution;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.JsonMappingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HashSet;
import java.util.List;
import java.util.Random;
import java.util.Set;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** RED contract for the P1-01 immutable execution manifest. */
class ImmutableExecutionManifestTest {
    private static final int SCHEMA_VERSION = 1;
    private static final String EXECUTION_ID = "execution-pr-42-v1";
    private static final long PROJECT_ID = 7L;
    private static final String REPOSITORY_ID = "github:codecrow/review-fixture";
    private static final long PULL_REQUEST_ID = 42L;
    private static final String BASE_SHA = "a".repeat(40);
    private static final String HEAD_SHA = "b".repeat(40);
    private static final String MERGE_BASE_SHA = "c".repeat(40);
    private static final String DIFF_ARTIFACT_ID = "diff-artifact-pr-42-v1";
    private static final byte[] RAW_DIFF = ("diff --git a/app.py b/app.py\n"
            + "+print('immutable snapshot')\n").getBytes(StandardCharsets.UTF_8);
    private static final String DIFF_DIGEST = sha256(RAW_DIFF);
    private static final long DIFF_BYTE_LENGTH = RAW_DIFF.length;
    private static final String DIFF_ARTIFACT_KIND = "raw-diff";
    private static final String DIFF_ARTIFACT_PRODUCER = "java-vcs-acquisition";
    private static final String DIFF_ARTIFACT_PRODUCER_VERSION = "p1-01-v1";
    private static final String ARTIFACT_SCHEMA_VERSION = "review-artifact-v1";
    private static final String POLICY_VERSION = "candidate-review-v2";
    private static final String CREATION_FENCE = "creation:00000017";
    private static final Instant CREATED_AT = Instant.parse("2026-07-15T12:00:00Z");
    private static final String EXPECTED_ARTIFACT_MANIFEST_DIGEST =
            "61a97dd9f2de76e3347b0261b8f049c56764a54d68d70e8d15e9491e29508b78";
    private static final ObjectMapper MAPPER = new ObjectMapper()
            .registerModule(new JavaTimeModule());

    @Test
    void constructsOneCanonicalValueAndRoundTripsThroughJackson() throws Exception {
        ImmutableExecutionManifest first = validInput().create();
        ImmutableExecutionManifest equalValue = validInput().create();

        assertThat(first.schemaVersion()).isEqualTo(SCHEMA_VERSION);
        assertThat(first.executionId()).isEqualTo(EXECUTION_ID);
        assertThat(first.projectId()).isEqualTo(PROJECT_ID);
        assertThat(first.repositoryId()).isEqualTo(REPOSITORY_ID);
        assertThat(first.pullRequestId()).isEqualTo(PULL_REQUEST_ID);
        assertThat(first.baseSha()).isEqualTo(BASE_SHA);
        assertThat(first.headSha()).isEqualTo(HEAD_SHA);
        assertThat(first.mergeBaseSha()).isEqualTo(MERGE_BASE_SHA);
        assertThat(first.diffArtifactId()).isEqualTo(DIFF_ARTIFACT_ID);
        assertThat(first.diffDigest()).isEqualTo(DIFF_DIGEST);
        assertThat(first.diffByteLength()).isEqualTo(DIFF_BYTE_LENGTH);
        assertThat(first.diffArtifactKind()).isEqualTo(DIFF_ARTIFACT_KIND);
        assertThat(first.diffArtifactProducer()).isEqualTo(DIFF_ARTIFACT_PRODUCER);
        assertThat(first.diffArtifactProducerVersion()).isEqualTo(DIFF_ARTIFACT_PRODUCER_VERSION);
        assertThat(first.artifactSchemaVersion()).isEqualTo(ARTIFACT_SCHEMA_VERSION);
        assertThat(first.policyVersion()).isEqualTo(POLICY_VERSION);
        assertThat(first.creationFence()).isEqualTo(CREATION_FENCE);
        assertThat(first.createdAt()).isEqualTo(CREATED_AT);
        assertThat(first.inputArtifacts()).singleElement().satisfies(entry -> {
            assertThat(entry.artifactId()).isEqualTo(DIFF_ARTIFACT_ID);
            assertThat(entry.contentKey()).isEqualTo("pull-request.diff");
            assertThat(entry.snapshotSha()).isEqualTo(HEAD_SHA);
            assertThat(entry.kind()).isEqualTo(ArtifactManifestEntry.Kind.RAW_DIFF);
        });
        assertThat(first.artifactManifestDigest()).matches("[0-9a-f]{64}");
        assertThat(first.artifactManifestDigest()).isEqualTo(EXPECTED_ARTIFACT_MANIFEST_DIGEST);
        assertThat(equalValue).isEqualTo(first);
        assertThat(equalValue.hashCode()).isEqualTo(first.hashCode());

        byte[] serialized = MAPPER.writeValueAsBytes(first);
        assertThat(MAPPER.writeValueAsBytes(equalValue)).isEqualTo(serialized);
        assertThat(MAPPER.readTree(serialized).path("createdAt").isTextual()).isTrue();
        assertThat(MAPPER.readTree(serialized).path("createdAt").asText())
                .isEqualTo("2026-07-15T12:00:00Z");
        ImmutableExecutionManifest restored = MAPPER.readValue(
                serialized, ImmutableExecutionManifest.class);

        assertThat(restored).isEqualTo(first);
        assertThat(restored.artifactManifestDigest()).isEqualTo(first.artifactManifestDigest());
        assertThatCode(() -> restored.requireSameCoordinates(first))
                .doesNotThrowAnyException();
    }

    @Test
    void javaSerializationMatchesTheSharedPythonContractFixture() throws Exception {
        JsonNode fixture;
        try (var input = getClass().getResourceAsStream(
                "/contracts/execution-manifest-v1.json")) {
            assertThat(input).isNotNull();
            fixture = MAPPER.readTree(input);
        }

        ImmutableExecutionManifest javaManifest = validInput().create();
        JsonNode serializedManifest = MAPPER.readTree(
                MAPPER.writeValueAsBytes(javaManifest));
        assertThat(serializedManifest)
                .isEqualTo(fixture.path("manifest"));
        javaManifest.verifyRawDiff(
                fixture.path("rawDiff").asText().getBytes(StandardCharsets.UTF_8));
    }

    @Test
    void rejectsMissingUnsupportedOrMalformedVersions() {
        assertInvalid(input -> input.schemaVersion = 0);
        assertInvalid(input -> input.schemaVersion = 2);
        assertInvalid(input -> input.artifactSchemaVersion = null);
        assertInvalid(input -> input.artifactSchemaVersion = "   ");
        assertInvalid(input -> input.artifactSchemaVersion = "review-artifact-v2");
        assertInvalid(input -> input.artifactSchemaVersion = "Review Artifact V1");
        assertInvalid(input -> input.policyVersion = null);
        assertInvalid(input -> input.policyVersion = "   ");
        assertInvalid(input -> input.policyVersion = "Candidate Review V2");
    }

    @Test
    void rejectsMissingOrUnstableExecutionRepositoryAndArtifactIdentifiers() {
        assertInvalid(input -> input.executionId = null);
        assertInvalid(input -> input.executionId = "   ");
        assertInvalid(input -> input.executionId = "execution with spaces");
        assertInvalid(input -> input.projectId = 0);
        assertInvalid(input -> input.projectId = -1);
        assertInvalid(input -> input.repositoryId = null);
        assertInvalid(input -> input.repositoryId = "   ");
        assertInvalid(input -> input.repositoryId = "refs/heads/main");
        assertInvalid(input -> input.repositoryId = "github:workspace/repository\nmain");
        assertInvalid(input -> input.pullRequestId = 0);
        assertInvalid(input -> input.pullRequestId = -1);
        assertInvalid(input -> input.diffArtifactId = null);
        assertInvalid(input -> input.diffArtifactId = "   ");
        assertInvalid(input -> input.diffArtifactId = "diff artifact with spaces");
        assertInvalid(input -> input.diffByteLength = -1L);
        assertInvalid(input -> input.diffArtifactKind = null);
        assertInvalid(input -> input.diffArtifactKind = "review-output");
        assertInvalid(input -> input.diffArtifactProducer = null);
        assertInvalid(input -> input.diffArtifactProducer = "mutable producer");
        assertInvalid(input -> input.diffArtifactProducerVersion = null);
        assertInvalid(input -> input.diffArtifactProducerVersion = "Producer V1");
    }

    @Test
    void requiresExactLowercaseFortyOrSixtyFourCharacterRevisions() {
        assertInvalid(input -> input.baseSha = null);
        assertInvalid(input -> input.baseSha = "a".repeat(39));
        assertInvalid(input -> input.baseSha = "A".repeat(40));
        assertInvalid(input -> input.headSha = null);
        assertInvalid(input -> input.headSha = "b".repeat(65));
        assertInvalid(input -> input.headSha = "B".repeat(40));
        assertInvalid(input -> input.mergeBaseSha = null);
        assertInvalid(input -> input.mergeBaseSha = "c".repeat(41));
        assertInvalid(input -> input.mergeBaseSha = "C".repeat(64));

        ManifestInput sixtyFourCharacterRevisions = validInput();
        sixtyFourCharacterRevisions.baseSha = "d".repeat(64);
        sixtyFourCharacterRevisions.headSha = "e".repeat(64);
        sixtyFourCharacterRevisions.mergeBaseSha = "f".repeat(64);
        assertThatCode(sixtyFourCharacterRevisions::create).doesNotThrowAnyException();
    }

    @Test
    void rejectsMissingMalformedOrTamperedDigests() {
        assertInvalid(input -> input.diffDigest = null);
        assertInvalid(input -> input.diffDigest = "d".repeat(63));
        assertInvalid(input -> input.diffDigest = "D".repeat(64));
        assertInvalid(input -> input.createdAt = null);

        ImmutableExecutionManifest manifest = validInput().create();
        ObjectNode missingArtifactManifestDigest = MAPPER.valueToTree(manifest);
        missingArtifactManifestDigest.remove("artifactManifestDigest");
        assertPersistedJsonRejected(missingArtifactManifestDigest);

        ObjectNode malformedArtifactManifestDigest = MAPPER.valueToTree(manifest);
        malformedArtifactManifestDigest.put("artifactManifestDigest", "not-a-sha256");
        assertPersistedJsonRejected(malformedArtifactManifestDigest);

        ObjectNode staleArtifactManifestDigest = MAPPER.valueToTree(manifest);
        staleArtifactManifestDigest.put("headSha", "d".repeat(40));
        assertPersistedJsonRejected(staleArtifactManifestDigest);
    }

    @Test
    void rejectsMissingOrMalformedCreationFence() {
        assertInvalid(input -> input.creationFence = null);
        assertInvalid(input -> input.creationFence = "   ");
        assertInvalid(input -> input.creationFence = "creation fence with spaces");
        assertInvalid(input -> input.creationFence = "creation:1\ncreation:2");
    }

    @Test
    void enforcesTheCanonicalCrossLanguageTimestampSpelling() {
        ManifestInput milliseconds = changed(
                input -> input.createdAt = Instant.parse("2026-07-15T12:00:00.123Z"));
        ManifestInput microseconds = changed(
                input -> input.createdAt = Instant.parse("2026-07-15T12:00:00.123456Z"));

        assertThat(MAPPER.valueToTree(milliseconds.create()).path("createdAt").asText())
                .isEqualTo("2026-07-15T12:00:00.123Z");
        assertThat(MAPPER.valueToTree(microseconds.create()).path("createdAt").asText())
                .isEqualTo("2026-07-15T12:00:00.123456Z");
        assertInvalid(input -> input.createdAt =
                Instant.parse("2026-07-15T12:00:00.123456789Z"));

        ImmutableExecutionManifest manifest = validInput().create();
        ObjectNode numeric = MAPPER.valueToTree(manifest);
        numeric.put("createdAt", 1_752_580_800L);
        assertPersistedJsonRejected(numeric);
        for (String invalid : List.of(
                "2026-07-15T15:00:00+03:00",
                "2026-07-15T12:00:00.000Z",
                "2026-07-15T12:00:00.123000Z",
                "2026-07-15T12:00:00.123456789Z")) {
            ObjectNode nonCanonical = MAPPER.valueToTree(manifest);
            nonCanonical.put("createdAt", invalid);
            assertPersistedJsonRejected(nonCanonical);
        }
    }

    @Test
    void rejectsMixedExpectedExecutionCoordinates() {
        ImmutableExecutionManifest manifest = validInput().create();
        ImmutableExecutionManifest sameCoordinates = validInput().create();
        ManifestInput mixedInput = validInput();
        mixedInput.headSha = "d".repeat(40);
        ImmutableExecutionManifest mixedCoordinates = mixedInput.create();

        assertThatCode(() -> manifest.requireSameCoordinates(sameCoordinates))
                .doesNotThrowAnyException();
        assertThatThrownBy(() -> manifest.requireSameCoordinates(mixedCoordinates))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> manifest.requireSameCoordinates(null))
                .isInstanceOfAny(IllegalArgumentException.class, NullPointerException.class);
    }

    @Test
    void verifiesTheRawDiffAgainstTheFrozenDiffDigest() {
        ImmutableExecutionManifest manifest = validInput().create();

        assertThatCode(() -> manifest.verifyRawDiff(RAW_DIFF.clone()))
                .doesNotThrowAnyException();
        assertThatThrownBy(() -> manifest.verifyRawDiff(
                "different raw diff".getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> manifest.verifyRawDiff(null))
                .isInstanceOfAny(IllegalArgumentException.class, NullPointerException.class);
    }

    @Test
    void rejectsMissingForeignDuplicateOrNonCanonicalInputArtifactInventories() {
        ImmutableExecutionManifest manifest = validInput().create();
        ArtifactManifestEntry rawDiff = manifest.inputArtifacts().get(0);
        ArtifactManifestEntry source = new ArtifactManifestEntry(
                EXECUTION_ID,
                "source:app-py",
                "app.py",
                HEAD_SHA,
                "0".repeat(64),
                1L,
                ArtifactManifestEntry.Kind.SOURCE_FILE,
                ARTIFACT_SCHEMA_VERSION,
                DIFF_ARTIFACT_PRODUCER,
                DIFF_ARTIFACT_PRODUCER_VERSION);

        assertThatCode(() -> createWithArtifacts(List.of(rawDiff, source)))
                .doesNotThrowAnyException();
        assertThatThrownBy(() -> new ImmutableExecutionManifest(
                manifest.schemaVersion(), manifest.executionId(), manifest.projectId(),
                manifest.repositoryId(), manifest.pullRequestId(), manifest.baseSha(),
                manifest.headSha(), manifest.mergeBaseSha(), manifest.diffArtifactId(),
                manifest.diffDigest(), manifest.diffByteLength(), manifest.diffArtifactKind(),
                manifest.diffArtifactProducer(), manifest.diffArtifactProducerVersion(),
                manifest.artifactSchemaVersion(), manifest.policyVersion(),
                manifest.creationFence(), manifest.createdAt(), List.of(source, rawDiff),
                createWithArtifacts(List.of(rawDiff, source)).artifactManifestDigest()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("canonical");
        assertThatThrownBy(() -> createWithArtifacts(List.of(rawDiff, rawDiff)))
                .isInstanceOf(IllegalArgumentException.class);
        ArtifactManifestEntry foreign = new ArtifactManifestEntry(
                "another-execution", source.artifactId(), source.contentKey(),
                source.snapshotSha(), source.contentDigest(), source.byteLength(),
                source.kind(), source.artifactSchemaVersion(), source.producer(),
                source.producerVersion());
        assertThatThrownBy(() -> createWithArtifacts(List.of(rawDiff, foreign)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("another execution");
    }

    @Test
    void everyChangedBoundFieldProducesADifferentManifestDigest() {
        String originalDigest = validInput().create().artifactManifestDigest();
        List<ManifestInput> changedInputs = List.of(
                changed(input -> input.executionId = "pr:execution-0002"),
                changed(input -> input.projectId = 42L),
                changed(input -> input.repositoryId = "github:codecrow/other-repository"),
                changed(input -> input.pullRequestId = 83L),
                changed(input -> input.baseSha = "d".repeat(40)),
                changed(input -> input.headSha = "e".repeat(40)),
                changed(input -> input.mergeBaseSha = "f".repeat(40)),
                changed(input -> input.diffArtifactId = "diff:pull-request-83"),
                changed(input -> input.diffDigest = "0".repeat(64)),
                changed(input -> input.diffByteLength = DIFF_BYTE_LENGTH + 1),
                changed(input -> input.diffArtifactProducer = "java-vcs-snapshot"),
                changed(input -> input.diffArtifactProducerVersion = "p1-01-v2"),
                changed(input -> input.policyVersion = "candidate-review-v3"),
                changed(input -> input.creationFence = "creation:00000002"),
                changed(input -> input.createdAt = CREATED_AT.plusSeconds(1)));

        Set<String> observedDigests = new HashSet<>();
        observedDigests.add(originalDigest);
        for (ManifestInput input : changedInputs) {
            String changedDigest = input.create().artifactManifestDigest();
            assertThat(changedDigest).isNotEqualTo(originalDigest);
            observedDigests.add(changedDigest);
        }
        assertThat(observedDigests).hasSize(changedInputs.size() + 1);
    }

    @Test
    void generatedValidIdentitiesPreserveEqualityAcrossJacksonRoundTrips()
            throws Exception {
        Random random = new Random(0x50101L);

        for (int sample = 0; sample < 256; sample++) {
            ManifestInput input = validInput();
            input.executionId = "execution:property:" + sample + ":"
                    + Long.toUnsignedString(random.nextLong(), 16);
            input.projectId = 1L + random.nextInt(1_000_000);
            input.repositoryId = "github:workspace-" + sample
                    + "/repository-" + random.nextInt(1_000_000);
            input.pullRequestId = 1L + random.nextInt(1_000_000);
            input.baseSha = randomSha(random, sample % 2 == 0 ? 40 : 64);
            input.headSha = randomSha(random, sample % 3 == 0 ? 64 : 40);
            input.mergeBaseSha = randomSha(random, sample % 5 == 0 ? 64 : 40);
            input.diffArtifactId = "diff:property:" + sample;
            input.diffDigest = randomSha(random, 64);
            input.diffByteLength = random.nextInt(1_000_000);
            input.policyVersion = "candidate-property-" + sample;
            input.creationFence = "creation:property:" + sample;
            input.createdAt = CREATED_AT.plusSeconds(sample);

            ImmutableExecutionManifest generated = input.create();
            ImmutableExecutionManifest restored = MAPPER.readValue(
                    MAPPER.writeValueAsBytes(generated),
                    ImmutableExecutionManifest.class);

            assertThat(restored).isEqualTo(generated);
            assertThat(restored.hashCode()).isEqualTo(generated.hashCode());
            assertThat(restored.artifactManifestDigest())
                    .isEqualTo(generated.artifactManifestDigest());
            assertThatCode(() -> restored.requireSameCoordinates(generated))
                    .doesNotThrowAnyException();
        }
    }

    private static String randomSha(Random random, int length) {
        char[] value = new char[length];
        String hex = "0123456789abcdef";
        for (int index = 0; index < value.length; index++) {
            value[index] = hex.charAt(random.nextInt(hex.length()));
        }
        return new String(value);
    }

    private static ManifestInput changed(Consumer<ManifestInput> change) {
        ManifestInput input = validInput();
        change.accept(input);
        return input;
    }

    private static void assertInvalid(Consumer<ManifestInput> change) {
        ManifestInput input = changed(change);
        assertThatThrownBy(input::create)
                .isInstanceOfAny(IllegalArgumentException.class, NullPointerException.class);
    }

    private static void assertPersistedJsonRejected(ObjectNode document) {
        assertThatThrownBy(() -> MAPPER.treeToValue(
                document, ImmutableExecutionManifest.class))
                .isInstanceOf(JsonMappingException.class);
    }

    private static ManifestInput validInput() {
        return new ManifestInput();
    }

    private static ImmutableExecutionManifest createWithArtifacts(
            List<ArtifactManifestEntry> artifacts) {
        return ImmutableExecutionManifest.create(
                SCHEMA_VERSION, EXECUTION_ID, PROJECT_ID, REPOSITORY_ID,
                PULL_REQUEST_ID, BASE_SHA, HEAD_SHA, MERGE_BASE_SHA,
                DIFF_ARTIFACT_ID, DIFF_DIGEST, DIFF_BYTE_LENGTH,
                DIFF_ARTIFACT_KIND, DIFF_ARTIFACT_PRODUCER,
                DIFF_ARTIFACT_PRODUCER_VERSION, ARTIFACT_SCHEMA_VERSION,
                POLICY_VERSION, CREATION_FENCE, CREATED_AT, artifacts);
    }

    private static String sha256(byte[] value) {
        try {
            return java.util.HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new AssertionError("SHA-256 must be present in the test runtime", error);
        }
    }

    private static final class ManifestInput {
        private int schemaVersion = SCHEMA_VERSION;
        private String executionId = EXECUTION_ID;
        private long projectId = PROJECT_ID;
        private String repositoryId = REPOSITORY_ID;
        private long pullRequestId = PULL_REQUEST_ID;
        private String baseSha = BASE_SHA;
        private String headSha = HEAD_SHA;
        private String mergeBaseSha = MERGE_BASE_SHA;
        private String diffArtifactId = DIFF_ARTIFACT_ID;
        private String diffDigest = DIFF_DIGEST;
        private long diffByteLength = DIFF_BYTE_LENGTH;
        private String diffArtifactKind = DIFF_ARTIFACT_KIND;
        private String diffArtifactProducer = DIFF_ARTIFACT_PRODUCER;
        private String diffArtifactProducerVersion = DIFF_ARTIFACT_PRODUCER_VERSION;
        private String artifactSchemaVersion = ARTIFACT_SCHEMA_VERSION;
        private String policyVersion = POLICY_VERSION;
        private String creationFence = CREATION_FENCE;
        private Instant createdAt = CREATED_AT;

        private ImmutableExecutionManifest create() {
            return ImmutableExecutionManifest.create(
                    schemaVersion,
                    executionId,
                    projectId,
                    repositoryId,
                    pullRequestId,
                    baseSha,
                    headSha,
                    mergeBaseSha,
                    diffArtifactId,
                    diffDigest,
                    diffByteLength,
                    diffArtifactKind,
                    diffArtifactProducer,
                    diffArtifactProducerVersion,
                    artifactSchemaVersion,
                    policyVersion,
                    creationFence,
                    createdAt);
        }
    }
}
