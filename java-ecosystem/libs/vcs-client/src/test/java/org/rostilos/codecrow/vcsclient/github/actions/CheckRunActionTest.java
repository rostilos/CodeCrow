package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateResult;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;

import java.io.IOException;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class CheckRunActionTest {

    @Mock private OkHttpClient httpClient;
    @Mock private Call call;

    private CheckRunAction action;
    private final ObjectMapper mapper = new ObjectMapper();

    @BeforeEach
    void setUp() {
        action = new CheckRunAction(httpClient);
    }

    private Response successResponse(Request req) {
        return new Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(201)
                .message("Created")
                .body(ResponseBody.create("{\"id\":1}", MediaType.parse("application/json")))
                .build();
    }

    private Response errorResponse(Request req, int code) {
        return new Response.Builder()
                .request(req)
                .protocol(Protocol.HTTP_1_1)
                .code(code)
                .message("Error")
                .body(ResponseBody.create("{\"message\":\"bad\"}", MediaType.parse("application/json")))
                .build();
    }

    private AnalysisSummary buildSummary(int high, int medium, int low, int resolved,
                                          int totalUnresolved, QualityGateResult qg) {
        return AnalysisSummary.builder()
                .withProjectNamespace("test-proj")
                .withPullRequestId(1L)
                .withComment("Test comment")
                .withPlatformAnalysisUrl("https://example.com/analysis")
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, high, ""))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, medium, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, low, ""))
                .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                .withResolvedIssues(new AnalysisSummary.SeverityMetric(null, resolved, ""))
                .withTotalIssues(high + medium + low + resolved)
                .withTotalUnresolvedIssues(totalUnresolved)
                .withIssues(List.of())
                .withFileIssueCount(Map.of())
                .withQualityGateResult(qg)
                .build();
    }

    // ── createCheckRun ───────────────────────────────────────────────────

    @Test
    void createCheckRun_success_shouldCallApi() throws Exception {
        AnalysisSummary summary = buildSummary(0, 0, 0, 0, 0, null);
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("owner", "repo", "abc123", summary);

        Request sent = captor.getValue();
        assertThat(sent.url().toString()).contains("/repos/owner/repo/check-runs");
        assertThat(sent.header("Accept")).contains("github");
    }

    @Test
    void createCheckRun_httpFailure_shouldThrowIOException() throws Exception {
        AnalysisSummary summary = buildSummary(1, 0, 0, 0, 1, null);
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> errorResponse(captor.getValue(), 422));

        assertThatThrownBy(() -> action.createCheckRun("o", "r", "sha", summary))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("422");
    }

    @Test
    void createCheckRun_requestBody_shouldContainHeadSha() throws Exception {
        AnalysisSummary summary = buildSummary(0, 0, 0, 0, 0, null);
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("owner", "repo", "deadbeef", summary);

        // Verify body content
        Request req = captor.getValue();
        okio.Buffer buf = new okio.Buffer();
        req.body().writeTo(buf);
        String body = buf.readUtf8();
        JsonNode json = mapper.readTree(body);
        assertThat(json.get("head_sha").asText()).isEqualTo("deadbeef");
        assertThat(json.get("name").asText()).isEqualTo("CodeCrow Analysis");
        assertThat(json.get("status").asText()).isEqualTo("completed");
    }

    // ── determineConclusion ──────────────────────────────────────────────

    @Nested
    class DetermineConclusion {

        @Test
        void qualityGatePassed_shouldReturnSuccess() throws Exception {
            QualityGateResult qg = new QualityGateResult(AnalysisResult.PASSED, "default", List.of());
            AnalysisSummary summary = buildSummary(5, 0, 0, 0, 5, qg);
            ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
            when(httpClient.newCall(captor.capture())).thenReturn(call);
            when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

            action.createCheckRun("o", "r", "sha", summary);

            okio.Buffer buf = new okio.Buffer();
            captor.getValue().body().writeTo(buf);
            JsonNode json = mapper.readTree(buf.readUtf8());
            assertThat(json.get("conclusion").asText()).isEqualTo("success");
        }

        @Test
        void qualityGateFailed_shouldReturnFailure() throws Exception {
            QualityGateResult qg = new QualityGateResult(AnalysisResult.FAILED, "strict", List.of());
            AnalysisSummary summary = buildSummary(5, 0, 0, 0, 5, qg);
            ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
            when(httpClient.newCall(captor.capture())).thenReturn(call);
            when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

            action.createCheckRun("o", "r", "sha", summary);

            okio.Buffer buf = new okio.Buffer();
            captor.getValue().body().writeTo(buf);
            JsonNode json = mapper.readTree(buf.readUtf8());
            assertThat(json.get("conclusion").asText()).isEqualTo("failure");
        }

        @Test
        void qualityGateSkipped_zeroIssues_shouldReturnSuccess() throws Exception {
            QualityGateResult qg = QualityGateResult.skipped();
            AnalysisSummary summary = buildSummary(0, 0, 0, 0, 0, qg);
            ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
            when(httpClient.newCall(captor.capture())).thenReturn(call);
            when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

            action.createCheckRun("o", "r", "sha", summary);

            okio.Buffer buf = new okio.Buffer();
            captor.getValue().body().writeTo(buf);
            JsonNode json = mapper.readTree(buf.readUtf8());
            assertThat(json.get("conclusion").asText()).isEqualTo("success");
        }

        @Test
        void noQualityGate_highIssues_shouldReturnFailure() throws Exception {
            AnalysisSummary summary = buildSummary(3, 0, 0, 0, 3, null);
            ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
            when(httpClient.newCall(captor.capture())).thenReturn(call);
            when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

            action.createCheckRun("o", "r", "sha", summary);

            okio.Buffer buf = new okio.Buffer();
            captor.getValue().body().writeTo(buf);
            JsonNode json = mapper.readTree(buf.readUtf8());
            assertThat(json.get("conclusion").asText()).isEqualTo("failure");
        }

        @Test
        void noQualityGate_mediumOnly_shouldReturnNeutral() throws Exception {
            AnalysisSummary summary = buildSummary(0, 2, 0, 0, 2, null);
            ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
            when(httpClient.newCall(captor.capture())).thenReturn(call);
            when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

            action.createCheckRun("o", "r", "sha", summary);

            okio.Buffer buf = new okio.Buffer();
            captor.getValue().body().writeTo(buf);
            JsonNode json = mapper.readTree(buf.readUtf8());
            assertThat(json.get("conclusion").asText()).isEqualTo("neutral");
        }

        @Test
        void noQualityGate_lowOnly_shouldReturnNeutral() throws Exception {
            AnalysisSummary summary = buildSummary(0, 0, 1, 0, 1, null);
            ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
            when(httpClient.newCall(captor.capture())).thenReturn(call);
            when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

            action.createCheckRun("o", "r", "sha", summary);

            okio.Buffer buf = new okio.Buffer();
            captor.getValue().body().writeTo(buf);
            JsonNode json = mapper.readTree(buf.readUtf8());
            assertThat(json.get("conclusion").asText()).isEqualTo("neutral");
        }
    }

    // ── buildTitle ───────────────────────────────────────────────────────

    @Test
    void zeroIssues_titleShouldSayNoIssues() throws Exception {
        AnalysisSummary summary = buildSummary(0, 0, 0, 0, 0, null);
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        assertThat(json.path("output").path("title").asText()).contains("No issues");
    }

    @Test
    void someIssues_titleShouldShowCount() throws Exception {
        AnalysisSummary summary = buildSummary(2, 1, 0, 0, 3, null);
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        assertThat(json.path("output").path("title").asText()).contains("3 issue(s)");
    }

    // ── buildAnnotations ─────────────────────────────────────────────────

    @Test
    void withIssues_shouldBuildAnnotations() throws Exception {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH, "BEST_PRACTICES", "src/Foo.java", 42,
                "Bad practice", "reason text", "fix it", null,
                "https://example.com/issue/1", 1L, "some snippet"
        );
        AnalysisSummary summary = AnalysisSummary.builder()
                .withProjectNamespace("ns")
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                .withTotalIssues(1)
                .withTotalUnresolvedIssues(1)
                .withIssues(List.of(issue))
                .withFileIssueCount(Map.of("src/Foo.java", 1))
                .build();

        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        JsonNode annotations = json.path("output").path("annotations");
        assertThat(annotations.isArray()).isTrue();
        assertThat(annotations.size()).isEqualTo(1);
        assertThat(annotations.get(0).get("path").asText()).isEqualTo("src/Foo.java");
        assertThat(annotations.get(0).get("start_line").asInt()).isEqualTo(42);
        assertThat(annotations.get(0).get("annotation_level").asText()).isEqualTo("failure");
    }

    @Test
    void leadingSlash_shouldBeStripped() throws Exception {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.MEDIUM, "BUG", "/src/Bar.java", 10,
                "Title", "reason", null, null,
                "url", 2L, "snippet"
        );
        AnalysisSummary summary = AnalysisSummary.builder()
                .withProjectNamespace("ns")
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 1, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                .withTotalIssues(1)
                .withTotalUnresolvedIssues(1)
                .withIssues(List.of(issue))
                .withFileIssueCount(Map.of())
                .build();

        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        assertThat(json.path("output").path("annotations").get(0).get("path").asText())
                .isEqualTo("src/Bar.java");
    }

    @Test
    void unanchoredIssue_shouldBeSkipped() throws Exception {
        // line <= 1, no codeSnippet → unanchored, should be skipped
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH, "BUG", "src/X.java", 1,
                "Title", "reason", null, null,
                "url", 3L, null
        );
        AnalysisSummary summary = AnalysisSummary.builder()
                .withProjectNamespace("ns")
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                .withTotalIssues(1)
                .withTotalUnresolvedIssues(1)
                .withIssues(List.of(issue))
                .withFileIssueCount(Map.of())
                .build();

        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        // annotations array should exist but be empty (or output shouldn't have annotations)
        JsonNode annotations = json.path("output").path("annotations");
        assertThat(annotations.isMissingNode() || annotations.size() == 0).isTrue();
    }

    @Test
    void annotationSeverityMapping_medium_shouldBeWarning() throws Exception {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.MEDIUM, "BUG", "src/M.java", 5,
                "Medium issue", "reason", null, null,
                "url", 4L, "code snippet"
        );
        AnalysisSummary summary = AnalysisSummary.builder()
                .withProjectNamespace("ns")
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 1, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                .withTotalIssues(1)
                .withTotalUnresolvedIssues(1)
                .withIssues(List.of(issue))
                .withFileIssueCount(Map.of())
                .build();

        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        assertThat(json.path("output").path("annotations").get(0).get("annotation_level").asText())
                .isEqualTo("warning");
    }

    @Test
    void annotationSeverityMapping_low_shouldBeNotice() throws Exception {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.LOW, "STYLE", "src/L.java", 3,
                "Low issue", "reason", null, null,
                "url", 5L, "snippet"
        );
        AnalysisSummary summary = AnalysisSummary.builder()
                .withProjectNamespace("ns")
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 1, ""))
                .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                .withTotalIssues(1)
                .withTotalUnresolvedIssues(1)
                .withIssues(List.of(issue))
                .withFileIssueCount(Map.of())
                .build();

        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        assertThat(json.path("output").path("annotations").get(0).get("annotation_level").asText())
                .isEqualTo("notice");
    }

    @Test
    void suggestedFix_shouldAddRawDetails() throws Exception {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH, "BUG", "src/F.java", 10,
                "Title", "reason", "Use method B instead", null,
                "url", 6L, "code"
        );
        AnalysisSummary summary = AnalysisSummary.builder()
                .withProjectNamespace("ns")
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 1, ""))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                .withTotalIssues(1)
                .withTotalUnresolvedIssues(1)
                .withIssues(List.of(issue))
                .withFileIssueCount(Map.of())
                .build();

        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        assertThat(json.path("output").path("annotations").get(0).has("raw_details")).isTrue();
        assertThat(json.path("output").path("annotations").get(0).get("raw_details").asText())
                .contains("Use method B instead");
    }

    // ── buildSummaryText / buildDetailedText ─────────────────────────────

    @Test
    void summaryText_zeroIssues_shouldShowGreatMessage() throws Exception {
        AnalysisSummary summary = buildSummary(0, 0, 0, 0, 0, null);
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        String summaryText = json.path("output").path("summary").asText();
        assertThat(summaryText).contains("No issues found");
    }

    @Test
    void summaryText_withIssues_shouldShowTable() throws Exception {
        AnalysisSummary summary = buildSummary(1, 2, 3, 1, 6, null);
        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        String summaryText = json.path("output").path("summary").asText();
        assertThat(summaryText).contains("High").contains("Medium").contains("Low");
    }

    @Test
    void detailedText_withComment_shouldIncludeIt() throws Exception {
        AnalysisSummary summary = AnalysisSummary.builder()
                .withProjectNamespace("ns")
                .withComment("This is a custom comment")
                .withHighSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.HIGH, 0, ""))
                .withMediumSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.MEDIUM, 0, ""))
                .withLowSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.LOW, 0, ""))
                .withInfoSeverityIssues(new AnalysisSummary.SeverityMetric(IssueSeverity.INFO, 0, ""))
                .withTotalIssues(0)
                .withTotalUnresolvedIssues(0)
                .withIssues(List.of())
                .withFileIssueCount(Map.of("src/A.java", 3, "src/B.java", 1))
                .build();

        ArgumentCaptor<Request> captor = ArgumentCaptor.forClass(Request.class);
        when(httpClient.newCall(captor.capture())).thenReturn(call);
        when(call.execute()).thenAnswer(inv -> successResponse(captor.getValue()));

        action.createCheckRun("o", "r", "sha", summary);

        okio.Buffer buf = new okio.Buffer();
        captor.getValue().body().writeTo(buf);
        JsonNode json = mapper.readTree(buf.readUtf8());
        String text = json.path("output").path("text").asText();
        assertThat(text).contains("This is a custom comment");
    }
}
