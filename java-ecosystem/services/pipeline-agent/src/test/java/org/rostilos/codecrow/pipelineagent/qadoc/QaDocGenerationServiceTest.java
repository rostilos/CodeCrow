package org.rostilos.codecrow.pipelineagent.qadoc;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.QaAutoDocConfig;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.taskmanagement.model.TaskDetails;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.time.OffsetDateTime;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.crypto.KeyGenerator;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("QaDocGenerationService")
class QaDocGenerationServiceTest {

    private MockWebServer mockWebServer;
    private QaDocGenerationService service;
    private final ObjectMapper mapper = new ObjectMapper();
    private Project project;

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();

        String baseUrl = mockWebServer.url("").toString();
        // Remove trailing slash
        if (baseUrl.endsWith("/")) {
            baseUrl = baseUrl.substring(0, baseUrl.length() - 1);
        }

        service = new QaDocGenerationService(baseUrl, createTestEncryptionService());

        project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);
        ReflectionTestUtils.setField(project, "name", "Test Project");
    }

    @AfterEach
    void tearDown() throws IOException {
        mockWebServer.shutdown();
    }

    private static TokenEncryptionService createTestEncryptionService() {
        try {
            KeyGenerator keyGen = KeyGenerator.getInstance("AES");
            keyGen.init(256);
            String testKey = Base64.getEncoder().encodeToString(keyGen.generateKey().getEncoded());
            return new TokenEncryptionService(testKey, null);
        } catch (Exception e) {
            throw new RuntimeException("Failed to create test encryption service", e);
        }
    }

    private QaAutoDocConfig baseConfig() {
        return new QaAutoDocConfig(
                true, 1L, "[A-Z]+-\\d+",
                QaAutoDocConfig.TaskIdSource.BRANCH_NAME,
                QaAutoDocConfig.TemplateMode.BASE, null,
                null
        );
    }

    private QaAutoDocConfig customTemplateConfig() {
        return new QaAutoDocConfig(
                true, 1L, "[A-Z]+-\\d+",
                QaAutoDocConfig.TaskIdSource.BRANCH_NAME,
                QaAutoDocConfig.TemplateMode.CUSTOM,
                "My custom QA template: {{task_id}}",
                null
        );
    }

    private TaskDetails sampleTaskDetails() {
        return new TaskDetails(
                "PROJ-123", "Implement feature", "Full description",
                "In Progress", "dev@test.com", "pm@test.com",
                "High", "Story", OffsetDateTime.now(), OffsetDateTime.now(),
                "https://test.atlassian.net/browse/PROJ-123"
        );
    }

    /**
     * Build a minimal QaDocGenerationContext for test cases.
     */
    private QaDocGenerationContext ctx(QaAutoDocConfig config, TaskDetails task,
                                       CodeAnalysis analysis, String diff, String prevDoc) {
        return QaDocGenerationContext.builder()
                .qaConfig(config)
                .taskDetails(task)
                .analysis(analysis)
                .diff(diff)
                .previousDocumentation(prevDoc)
                .build();
    }

    // ════════════════════════════════════════════════════════════════

    @Nested
    @DisplayName("generateQaDocumentation(raw params)")
    class GenerateFromRawParams {

        @Test
        @DisplayName("should send correct payload and return documentation")
        void shouldSendCorrectPayloadAndReturnDoc() throws Exception {
            String responseJson = mapper.writeValueAsString(Map.of(
                    "documentation", "## QA Steps\n\n1. Test login",
                    "documentation_needed", true
            ));
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody(responseJson));

            Map<String, Object> prMetadata = Map.of("sourceBranch", "feature/PROJ-123");

            String result = service.generateQaDocumentation(
                    project, 7L, 5, 3, prMetadata, ctx(baseConfig(), sampleTaskDetails(), null, null, null)
            );

            assertThat(result).isEqualTo("## QA Steps\n\n1. Test login");

            // Verify the request payload
            RecordedRequest request = mockWebServer.takeRequest();
            assertThat(request.getPath()).isEqualTo("/qa-documentation");
            assertThat(request.getHeader("Content-Type")).isEqualTo("application/json");

            JsonNode body = mapper.readTree(request.getBody().readUtf8());
            assertThat(body.get("project_id").asLong()).isEqualTo(42L);
            assertThat(body.get("project_name").asText()).isEqualTo("Test Project");
            assertThat(body.get("pr_number").asLong()).isEqualTo(7L);
            assertThat(body.get("issues_found").asInt()).isEqualTo(5);
            assertThat(body.get("files_analyzed").asInt()).isEqualTo(3);
            assertThat(body.get("template_mode").asText()).isEqualTo("BASE");
            assertThat(body.has("pr_metadata")).isTrue();
            assertThat(body.get("pr_metadata").get("sourceBranch").asText()).isEqualTo("feature/PROJ-123");

            // Verify task context (keys must match Python placeholder names)
            JsonNode taskContext = body.get("task_context");
            assertThat(taskContext).isNotNull();
            assertThat(taskContext.get("task_key").asText()).isEqualTo("PROJ-123");
            assertThat(taskContext.get("task_summary").asText()).isEqualTo("Implement feature");
            assertThat(taskContext.get("priority").asText()).isEqualTo("High");
        }

        @Test
        @DisplayName("should include custom template in payload when mode is CUSTOM")
        void shouldIncludeCustomTemplate() throws Exception {
            String responseJson = mapper.writeValueAsString(Map.of(
                    "documentation", "Custom doc output",
                    "documentation_needed", true
            ));
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody(responseJson));

            String result = service.generateQaDocumentation(
                    project, 1L, 0, 0, Map.of(), ctx(customTemplateConfig(), null, null, null, null)
            );

            assertThat(result).isEqualTo("Custom doc output");

            RecordedRequest request = mockWebServer.takeRequest();
            JsonNode body = mapper.readTree(request.getBody().readUtf8());
            assertThat(body.get("template_mode").asText()).isEqualTo("CUSTOM");
            assertThat(body.get("custom_template").asText()).isEqualTo("My custom QA template: {{task_id}}");
        }

        @Test
        @DisplayName("should omit task_context when taskDetails is null")
        void shouldOmitTaskContextWhenNull() throws Exception {
            String responseJson = mapper.writeValueAsString(Map.of(
                    "documentation", "Doc without task",
                    "documentation_needed", true
            ));
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody(responseJson));

            String result = service.generateQaDocumentation(
                    project, 1L, 0, 0, Map.of(), ctx(baseConfig(), null, null, null, null)
            );

            assertThat(result).isEqualTo("Doc without task");

            RecordedRequest request = mockWebServer.takeRequest();
            JsonNode body = mapper.readTree(request.getBody().readUtf8());
            assertThat(body.has("task_context")).isFalse();
        }

        @Test
        @DisplayName("should omit pr_metadata when empty")
        void shouldOmitPrMetadataWhenEmpty() throws Exception {
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody("{\"documentation\": \"doc\", \"documentation_needed\": true}"));

            service.generateQaDocumentation(project, 1L, 0, 0, Map.of(), ctx(baseConfig(), null, null, null, null));

            RecordedRequest request = mockWebServer.takeRequest();
            JsonNode body = mapper.readTree(request.getBody().readUtf8());
            assertThat(body.has("pr_metadata")).isFalse();
        }

        @Test
        @DisplayName("should return null when documentation_needed is false")
        void shouldReturnNullWhenNotNeeded() throws Exception {
            String responseJson = mapper.writeValueAsString(Map.of(
                    "documentation", "some text",
                    "documentation_needed", false
            ));
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody(responseJson));

            String result = service.generateQaDocumentation(
                    project, 1L, 0, 0, Map.of(), ctx(baseConfig(), null, null, null, null)
            );

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return null when server returns non-200 non-5xx")
        void shouldReturnNullForNon200() throws Exception {
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(400)
                    .setBody("Bad request"));

            String result = service.generateQaDocumentation(
                    project, 1L, 0, 0, Map.of(), ctx(baseConfig(), null, null, null, null)
            );

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should throw IOException when server returns 5xx")
        void shouldThrowOn5xx() {
            // RetryExecutor will retry on 5xx, but all attempts return 500 → IOException
            mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("Internal error"));
            mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("Internal error"));
            mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("Internal error"));
            mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("Internal error"));

            assertThatThrownBy(() ->
                    service.generateQaDocumentation(project, 1L, 0, 0, Map.of(), ctx(baseConfig(), null, null, null, null))
            ).isInstanceOf(Exception.class);
        }

        @Test
        @DisplayName("should return null when JSON parsing fails (rejects malformed responses)")
        void shouldReturnRawResponseWhenJsonInvalid() throws Exception {
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "text/plain")
                    .setBody("Plain text documentation"));

            String result = service.generateQaDocumentation(
                    project, 1L, 0, 0, Map.of(), ctx(baseConfig(), null, null, null, null)
            );

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return null when response body is empty")
        void shouldReturnNullWhenEmptyBody() throws Exception {
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setBody(""));

            String result = service.generateQaDocumentation(
                    project, 1L, 0, 0, Map.of(), ctx(baseConfig(), null, null, null, null)
            );

            assertThat(result).isNull();
        }
    }

    // ════════════════════════════════════════════════════════════════

    @Nested
    @DisplayName("buildAnalysisSummary")
    class BuildAnalysisSummaryTests {

        @Test
        @DisplayName("should return fallback when analysis is null")
        void shouldHandleNullAnalysis() {
            String result = QaDocGenerationService.buildAnalysisSummary(null);
            assertThat(result).isEqualTo("No analysis data available.");
        }

        @Test
        @DisplayName("should return fallback when no issues")
        void shouldHandleEmptyIssues() {
            CodeAnalysis analysis = new CodeAnalysis();
            String result = QaDocGenerationService.buildAnalysisSummary(analysis);
            assertThat(result).isEqualTo("No issues found in this analysis.");
        }

        @Test
        @DisplayName("should return resolved message when all issues resolved")
        void shouldHandleAllResolved() {
            CodeAnalysis analysis = new CodeAnalysis();
            CodeAnalysisIssue resolved = new CodeAnalysisIssue();
            resolved.setSeverity(IssueSeverity.HIGH);
            resolved.setFilePath("Test.java");
            resolved.setResolved(true);
            analysis.addIssue(resolved);

            String result = QaDocGenerationService.buildAnalysisSummary(analysis);
            assertThat(result).contains("have been resolved");
        }

        @Test
        @DisplayName("should include severity breakdown and file-grouped issues")
        void shouldBuildRichSummary() {
            CodeAnalysis analysis = new CodeAnalysis();

            CodeAnalysisIssue highIssue = new CodeAnalysisIssue();
            highIssue.setSeverity(IssueSeverity.HIGH);
            highIssue.setFilePath("src/Main.java");
            highIssue.setTitle("SQL Injection Risk");
            highIssue.setReason("User input is concatenated into SQL query without sanitization.");
            highIssue.setSuggestedFixDescription("Use parameterized queries.");
            highIssue.setIssueCategory(IssueCategory.SECURITY);
            highIssue.setLineNumber(42);
            analysis.addIssue(highIssue);

            CodeAnalysisIssue medIssue = new CodeAnalysisIssue();
            medIssue.setSeverity(IssueSeverity.MEDIUM);
            medIssue.setFilePath("src/Main.java");
            medIssue.setTitle("Missing null check");
            medIssue.setReason("Parameter 'config' could be null.");
            medIssue.setIssueCategory(IssueCategory.BUG_RISK);
            medIssue.setLineNumber(78);
            analysis.addIssue(medIssue);

            CodeAnalysisIssue lowIssue = new CodeAnalysisIssue();
            lowIssue.setSeverity(IssueSeverity.LOW);
            lowIssue.setFilePath("src/Utils.java");
            lowIssue.setTitle("Unused import");
            lowIssue.setReason("Import 'java.util.List' is not used.");
            lowIssue.setIssueCategory(IssueCategory.STYLE);
            lowIssue.setLineNumber(3);
            analysis.addIssue(lowIssue);

            String result = QaDocGenerationService.buildAnalysisSummary(analysis);

            // Severity breakdown
            assertThat(result).contains("**HIGH**: 1");
            assertThat(result).contains("**MEDIUM**: 1");
            assertThat(result).contains("**LOW**: 1");

            // File grouping
            assertThat(result).contains("`src/Main.java`");
            assertThat(result).contains("`src/Utils.java`");

            // Issue details
            assertThat(result).contains("[HIGH]");
            assertThat(result).contains("SQL Injection Risk");
            assertThat(result).contains("Security");
            assertThat(result).contains("parameterized queries");
            assertThat(result).contains("Line: 42");

            assertThat(result).contains("[MEDIUM]");
            assertThat(result).contains("Missing null check");

            assertThat(result).contains("[LOW]");
            assertThat(result).contains("Unused import");
        }

        @Test
        @DisplayName("should include analysisSummary in payload when CodeAnalysis is provided")
        void shouldIncludeAnalysisSummaryInPayload() throws Exception {
            // Setup analysis with a single issue
            CodeAnalysis analysis = new CodeAnalysis();
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            issue.setSeverity(IssueSeverity.HIGH);
            issue.setFilePath("App.java");
            issue.setTitle("Critical bug");
            issue.setReason("Null dereference on line 10.");
            issue.setIssueCategory(IssueCategory.BUG_RISK);
            issue.setLineNumber(10);
            analysis.addIssue(issue);

            String responseJson = mapper.writeValueAsString(Map.of(
                    "documentation", "## QA with analysis",
                    "documentation_needed", true
            ));
            mockWebServer.enqueue(new MockResponse()
                    .setResponseCode(200)
                    .setHeader("Content-Type", "application/json")
                    .setBody(responseJson));

            Map<String, Object> prMetadata = new LinkedHashMap<>();
            prMetadata.put("sourceBranch", "feature/TEST-1");

            service.generateQaDocumentation(
                    project, 1L, 1, 1, prMetadata, ctx(baseConfig(), null, analysis, null, null));

            RecordedRequest request = mockWebServer.takeRequest();
            JsonNode body = mapper.readTree(request.getBody().readUtf8());

            // Verify analysisSummary is present in pr_metadata
            JsonNode prMeta = body.get("pr_metadata");
            assertThat(prMeta).isNotNull();
            assertThat(prMeta.has("analysisSummary")).isTrue();
            String summary = prMeta.get("analysisSummary").asText();
            assertThat(summary).contains("Critical bug");
            assertThat(summary).contains("App.java");
            assertThat(summary).contains("[HIGH]");
            assertThat(summary).contains("Null dereference");
        }
    }
}
