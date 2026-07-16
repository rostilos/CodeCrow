package org.rostilos.codecrow.analysisengine.aiclient;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchor;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorKind;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageWorkPlan;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.execution.ExecutionInputArtifactBundle;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.analysisengine.policy.ExecutionMode;
import org.rostilos.codecrow.analysisengine.policy.PolicyExecution;
import org.rostilos.codecrow.analysisengine.policy.PolicySelectionReason;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;
import org.springframework.web.client.RestTemplate;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AiAnalysisClientExecutionManifestQueueContractTest {
    private static final String RAW_DIFF = "diff --git a/src/Main.java b/src/Main.java\n"
            + "@@ -1 +1 @@\n-class Main {}\n+class Main { int value = 1; }\n";
    private static final String SOURCE_PATH = "src/Main.java";
    private static final String SOURCE_CONTENT = "class Main { int value = 1; }\n";
    private static final String BASE_SHA = "a".repeat(40);
    private static final String HEAD_SHA = "b".repeat(40);
    private static final String MERGE_BASE_SHA = "c".repeat(40);
    private static final String EXECUTION_ID = "execution-pr-2-v1";
    private static final String INDEX_VERSION = "rag-disabled";
    private static final String DIFF_ARTIFACT_ID = "diff-artifact-pr-2-v1";
    private static final String ARTIFACT_SCHEMA_VERSION = "review-artifact-v1";
    private static final String ARTIFACT_PRODUCER = "analysis-engine";
    private static final String ARTIFACT_PRODUCER_VERSION = "2026.07.15";
    private static final Instant CREATED_AT = Instant.parse("2026-07-15T12:00:00Z");

    @Mock
    private RestTemplate restTemplate;

    @Mock
    private RedisQueueService queueService;

    private ObjectMapper objectMapper;
    private AiAnalysisClient client;
    private AiAnalysisRequest request;
    private ImmutableExecutionManifest manifest;
    private PolicyExecution policy;
    private CoverageWorkPlan workPlan;

    @BeforeEach
    void setUp() {
        objectMapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        client = new AiAnalysisClient(restTemplate, queueService, objectMapper);
        request = requestBuilder().build();
        manifest = manifestFixture();
        policy = new PolicyExecution(
                EXECUTION_ID,
                manifest.policyVersion(),
                ExecutionMode.ACTIVE,
                PolicySelectionReason.ACTIVE_ROLLOUT_SELECTED,
                412,
                true,
                CREATED_AT);
        workPlan = coverageWorkPlanFixture(manifest);
    }

    @Test
    void queuesOneExactV2EnvelopeAndReturnsItsCoverageReceipt() throws Exception {
        Map<String, Object> receipt = coverageReceipt(
                workPlan, CoverageAnchorState.EXAMINED, null);
        stubFinal(receipt);

        Map<String, Object> result = performCandidate(request, ignored -> { });

        ArgumentCaptor<String> payload = ArgumentCaptor.forClass(String.class);
        verify(queueService).leftPush(eq("codecrow:analysis:jobs"), payload.capture());

        JsonNode queued = objectMapper.readTree(payload.getValue());
        JsonNode queuedRequest = queued.path("request");
        JsonNode queuedManifest = queuedRequest.path("executionManifest");
        JsonNode queuedLedger = queuedRequest.path("coverageLedger");

        assertThat(queued.path("schemaVersion").asInt()).isEqualTo(2);
        assertThat(queued.path("job_id").asText())
                .isNotBlank()
                .isNotEqualTo(manifest.executionId());
        assertThat(queuedManifest.toString())
                .isEqualTo(objectMapper.writeValueAsString(manifest));
        assertThat(queuedManifest.path("inputArtifacts").size()).isEqualTo(3);
        assertThat(queuedRequest.path("rawDiff").asText()).isEqualTo(RAW_DIFF);
        assertThat(queuedRequest.path("changedFiles"))
                .isEqualTo(objectMapper.valueToTree(List.of(SOURCE_PATH)));
        assertThat(queuedRequest.path("enrichmentData").path("fileContents").get(0)
                .path("content").asText()).isEqualTo(SOURCE_CONTENT);
        assertThat(queuedLedger.path("executionId").asText())
                .isEqualTo(workPlan.executionId());
        assertThat(queuedLedger.path("artifactManifestDigest").asText())
                .isEqualTo(workPlan.artifactManifestDigest());
        assertThat(queuedLedger.path("diffDigest").asText())
                .isEqualTo(workPlan.diffDigest());
        assertThat(queuedLedger.path("ledgerDigest").asText())
                .isEqualTo(workPlan.ledgerDigest());
        assertThat(queuedLedger.path("anchors"))
                .isEqualTo(objectMapper.valueToTree(workPlan.anchors()));
        assertThat(queuedRequest.has("reviewModelPass")).isFalse();
        assertThat(queuedRequest.has("reviewIndependentVerification")).isFalse();
        assertThat(queuedRequest.has("reviewExplorationEnabled")).isFalse();
        assertThat(objectMapper.writeValueAsString(result.get("coverageReceipt")))
                .isEqualTo(objectMapper.writeValueAsString(receipt));
    }

    @Test
    void acceptsTruthfulPartialCoverageWithoutChangingTheAnalysisResult()
            throws Exception {
        Map<String, Object> receipt = coverageReceipt(
                workPlan,
                CoverageAnchorState.INCOMPLETE,
                "analysis_budget_exhausted");
        stubFinal(receipt);

        Map<String, Object> result = performCandidate(request, ignored -> { });

        assertThat(result).containsEntry("comment", "reviewed");
        Map<?, ?> returnedReceipt = (Map<?, ?>) result.get("coverageReceipt");
        assertThat(returnedReceipt.get("analysisState")).isEqualTo("PARTIAL");
        assertThat(returnedReceipt.get("incomplete")).isEqualTo(1);
    }

    @Test
    void rejectsRawDiffAndSourceInventorySubstitutionBeforeQueueing() {
        AiAnalysisRequest changedDiff = requestBuilder()
                .withRawDiff(RAW_DIFF + "+tampered\n")
                .build();
        AiAnalysisRequest changedSourceInventory = requestBuilder()
                .withChangedFiles(List.of("src/Substituted.java"))
                .build();

        assertThatThrownBy(() -> performCandidate(changedDiff, ignored -> { }))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("raw diff");
        assertThatThrownBy(() -> performCandidate(
                changedSourceInventory, ignored -> { }))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("changedFiles");
        verify(queueService, never()).leftPush(anyString(), anyString());
    }

    @Test
    void rejectsTerminalResultWithoutCoverageReceiptBeforeForwardingIt()
            throws Exception {
        List<Map<String, Object>> forwarded = new ArrayList<>();
        stubEvent(Map.of(
                "type", "final",
                "executionId", manifest.executionId(),
                "artifactManifestDigest", manifest.artifactManifestDigest(),
                "result", Map.of("comment", "reviewed", "issues", List.of())));

        assertThatThrownBy(() -> performCandidate(request, forwarded::add))
                .isInstanceOf(java.io.IOException.class)
                .hasMessageContaining("coverageReceipt");
        assertThat(forwarded).isEmpty();
    }

    @Test
    void rejectsReceiptBoundToAnotherLedgerBeforeForwardingIt() throws Exception {
        List<Map<String, Object>> forwarded = new ArrayList<>();
        Map<String, Object> receipt = new LinkedHashMap<>(coverageReceipt(
                workPlan, CoverageAnchorState.EXAMINED, null));
        receipt.put("ledgerDigest", "f".repeat(64));
        stubFinal(receipt);

        assertThatThrownBy(() -> performCandidate(request, forwarded::add))
                .isInstanceOf(java.io.IOException.class)
                .hasMessageContaining("ledgerDigest");
        assertThat(forwarded).isEmpty();
    }

    @Test
    void rejectsFalseCoverageAggregateBeforeForwardingIt() throws Exception {
        List<Map<String, Object>> forwarded = new ArrayList<>();
        Map<String, Object> receipt = new LinkedHashMap<>(coverageReceipt(
                workPlan, CoverageAnchorState.EXAMINED, null));
        receipt.put("analysisState", "PARTIAL");
        stubFinal(receipt);

        assertThatThrownBy(() -> performCandidate(request, forwarded::add))
                .isInstanceOf(java.io.IOException.class)
                .hasMessageContaining("analysisState");
        assertThat(forwarded).isEmpty();
    }

    @Test
    void forwardsIdentityBoundProgressThenReturnsTheFinalAnalysis() throws Exception {
        Map<String, Object> progress = Map.of(
                "type", "progress",
                "executionId", manifest.executionId(),
                "artifactManifestDigest", manifest.artifactManifestDigest(),
                "percent", 50);
        Map<String, Object> receipt = coverageReceipt(
                workPlan, CoverageAnchorState.EXAMINED, null);
        when(queueService.rightPop(anyString(), anyLong()))
                .thenReturn(objectMapper.writeValueAsString(progress))
                .thenReturn(objectMapper.writeValueAsString(finalEvent(receipt)));
        List<Map<String, Object>> forwarded = new ArrayList<>();

        Map<String, Object> result = performCandidate(request, forwarded::add);

        assertThat(forwarded).hasSize(2);
        assertThat(forwarded.get(0)).containsEntry("type", "progress");
        assertThat(forwarded.get(1)).containsEntry("type", "final");
        assertThat(result).containsEntry("comment", "reviewed");
    }

    @Test
    void rejectsAnyCandidateEventThatCannotProveManifestIdentity() throws Exception {
        stubEvent(Map.of("type", "progress", "percent", 10));

        assertThatThrownBy(() -> performCandidate(request, ignored -> { }))
                .isInstanceOf(java.io.IOException.class)
                .hasMessageContaining("executionId");
    }

    @Test
    void rejectsMalformedCandidateEventJson() {
        when(queueService.rightPop(anyString(), anyLong()))
                .thenReturn("{not-json");

        assertThatThrownBy(() -> performCandidate(request, ignored -> { }))
                .isInstanceOf(java.io.IOException.class)
                .hasMessageContaining("malformed");
    }

    @Test
    void returnsOnlyAValidIdentityBoundSupersededControl() throws Exception {
        stubEvent(Map.of(
                "type", "superseded",
                "executionId", manifest.executionId(),
                "artifactManifestDigest", manifest.artifactManifestDigest(),
                "reasonCode", "latest_head_advanced",
                "computeState", "cancelled"));

        Map<String, Object> result = performCandidate(request, ignored -> { });

        assertThat(result)
                .containsEntry("status", "superseded")
                .containsEntry("reason", "latest_head_advanced")
                .containsEntry("computeState", "cancelled");
    }

    @Test
    void rejectsSupersededControlForAnotherManifest() throws Exception {
        stubEvent(Map.of(
                "type", "superseded",
                "executionId", manifest.executionId(),
                "artifactManifestDigest", "f".repeat(64),
                "reasonCode", "latest_head_advanced",
                "computeState", "cancelled"));

        assertThatThrownBy(() -> performCandidate(request, ignored -> { }))
                .isInstanceOf(java.io.IOException.class)
                .hasMessageContaining("artifactManifestDigest");
    }

    @Test
    void propagatesIdentityBoundProducerFailure() throws Exception {
        stubEvent(Map.of(
                "type", "error",
                "executionId", manifest.executionId(),
                "artifactManifestDigest", manifest.artifactManifestDigest(),
                "message", "provider failed"));

        assertThatThrownBy(() -> performCandidate(request, ignored -> { }))
                .isInstanceOf(java.io.IOException.class)
                .hasMessageContaining("provider failed");
    }

    @Test
    void failsClosedWhenTheCandidateEventHandlerRejectsAValidEvent()
            throws Exception {
        stubEvent(Map.of(
                "type", "progress",
                "executionId", manifest.executionId(),
                "artifactManifestDigest", manifest.artifactManifestDigest(),
                "percent", 10));

        assertThatThrownBy(() -> performCandidate(
                request,
                ignored -> {
                    throw new IllegalStateException("handler rejected event");
                }))
                .isInstanceOf(java.io.IOException.class)
                .hasMessageContaining("event handler rejected");
    }

    private Map<String, Object> performCandidate(
            AiAnalysisRequest candidateRequest,
            java.util.function.Consumer<Map<String, Object>> eventHandler)
            throws Exception {
        return client.performAnalysis(
                candidateRequest,
                eventHandler,
                policy,
                INDEX_VERSION,
                manifest,
                workPlan);
    }

    private void stubFinal(Map<String, Object> receipt) throws Exception {
        stubEvent(finalEvent(receipt));
    }

    private void stubEvent(Map<String, Object> event) throws Exception {
        when(queueService.rightPop(anyString(), anyLong()))
                .thenReturn(objectMapper.writeValueAsString(event));
    }

    private Map<String, Object> finalEvent(Map<String, Object> receipt) {
        return Map.of(
                "type", "final",
                "executionId", manifest.executionId(),
                "artifactManifestDigest", manifest.artifactManifestDigest(),
                "result", Map.of(
                        "comment", "reviewed",
                        "issues", List.of(),
                        "coverageReceipt", receipt));
    }

    private static CoverageWorkPlan coverageWorkPlanFixture(
            ImmutableExecutionManifest manifest) {
        CoverageAnchor anchor = new CoverageAnchor(
                sha256("anchor:" + SOURCE_PATH + ":1"),
                manifest.executionId(),
                sha256("hunk:" + SOURCE_PATH + ":1"),
                sha256("change:" + SOURCE_PATH),
                CoverageAnchorKind.TEXT_HUNK,
                SOURCE_PATH,
                SOURCE_PATH,
                1,
                1,
                1,
                1,
                ExactDiffInventory.ChangeStatus.MODIFY,
                manifest.diffArtifactId(),
                manifest.diffDigest(),
                true,
                CoverageAnchorState.PENDING,
                null);
        return new CoverageWorkPlan(
                1,
                manifest.executionId(),
                manifest.artifactManifestDigest(),
                manifest.diffDigest(),
                manifest.diffByteLength(),
                sha256("ledger:" + anchor.anchorId()),
                List.of(anchor));
    }

    private static Map<String, Object> coverageReceipt(
            CoverageWorkPlan workPlan,
            CoverageAnchorState terminalState,
            String reasonCode) {
        boolean complete = terminalState == CoverageAnchorState.EXAMINED;
        boolean failed = terminalState == CoverageAnchorState.FAILED;
        Map<String, Object> disposition = new LinkedHashMap<>();
        disposition.put("anchorId", workPlan.anchors().get(0).anchorId());
        disposition.put("state", terminalState.name());
        disposition.put("reasonCode", reasonCode);

        Map<String, Object> receipt = new LinkedHashMap<>();
        receipt.put("schemaVersion", workPlan.schemaVersion());
        receipt.put("executionId", workPlan.executionId());
        receipt.put("artifactManifestDigest", workPlan.artifactManifestDigest());
        receipt.put("diffDigest", workPlan.diffDigest());
        receipt.put("diffByteLength", workPlan.diffByteLength());
        receipt.put("ledgerDigest", workPlan.ledgerDigest());
        receipt.put("analysisState", complete ? "COMPLETE" : failed ? "FAILED" : "PARTIAL");
        receipt.put("total", 1);
        receipt.put("pending", 0);
        receipt.put("ownerPending", 0);
        receipt.put("examined", complete ? 1 : 0);
        receipt.put("incomplete", terminalState == CoverageAnchorState.INCOMPLETE ? 1 : 0);
        receipt.put("unsupported", terminalState == CoverageAnchorState.UNSUPPORTED ? 1 : 0);
        receipt.put("failed", failed ? 1 : 0);
        receipt.put("policyExcluded", 0);
        receipt.put("deletedRecorded", 0);
        receipt.put("dispositions", List.of(disposition));
        return receipt;
    }

    private static AiAnalysisRequestImpl.Builder<?> requestBuilder() {
        return AiAnalysisRequestImpl.builder()
                .withProjectId(1L)
                .withPullRequestId(2L)
                .withProjectVcsConnectionBindingInfo("ws", "repo")
                .withProjectMetadata("Codecrow", "codecrow-garden")
                .withProjectAiConnectionTokenDecrypted("key")
                .withProjectVcsConnectionCredentials("client", "secret")
                .withAccessToken("token")
                .withMaxAllowedTokens(1000)
                .withVcsProvider("github")
                .withRawDiff(RAW_DIFF)
                .withImmutableSnapshot(BASE_SHA, HEAD_SHA, MERGE_BASE_SHA)
                .withPreviousCommitHash(BASE_SHA)
                .withCurrentCommitHash(HEAD_SHA)
                .withAnalysisMode(
                        org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode.FULL)
                .withAnalysisType(
                        org.rostilos.codecrow.core.model.codeanalysis.AnalysisType.PR_REVIEW)
                .withChangedFiles(List.of(SOURCE_PATH))
                .withDeletedFiles(List.of())
                .withDiffSnippets(List.of())
                .withEnrichmentData(enrichmentFixture());
    }

    private static PrEnrichmentDataDto enrichmentFixture() {
        long contentBytes = SOURCE_CONTENT.getBytes(StandardCharsets.UTF_8).length;
        return new PrEnrichmentDataDto(
                List.of(FileContentDto.of(SOURCE_PATH, SOURCE_CONTENT)),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        1, 1, 0, 0, contentBytes, 0, Map.of()));
    }

    private static ImmutableExecutionManifest manifestFixture() {
        PrEnrichmentDataDto enrichment = enrichmentFixture();
        ExecutionInputArtifactBundle inputs = ExecutionInputArtifactBundle.create(
                EXECUTION_ID,
                HEAD_SHA,
                DIFF_ARTIFACT_ID,
                RAW_DIFF.getBytes(StandardCharsets.UTF_8),
                enrichment,
                ARTIFACT_SCHEMA_VERSION,
                ARTIFACT_PRODUCER,
                ARTIFACT_PRODUCER_VERSION);
        return ImmutableExecutionManifest.create(
                ImmutableExecutionManifest.CURRENT_SCHEMA_VERSION,
                EXECUTION_ID,
                1L,
                "github:ws/repo",
                2L,
                BASE_SHA,
                HEAD_SHA,
                MERGE_BASE_SHA,
                DIFF_ARTIFACT_ID,
                sha256(RAW_DIFF),
                RAW_DIFF.getBytes(StandardCharsets.UTF_8).length,
                "raw-diff",
                ARTIFACT_PRODUCER,
                ARTIFACT_PRODUCER_VERSION,
                ARTIFACT_SCHEMA_VERSION,
                "candidate-review-v2",
                "creation:00000017",
                CREATED_AT,
                inputs.entries());
    }

    private static String sha256(String value) {
        return sha256(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String sha256(byte[] value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }
}
