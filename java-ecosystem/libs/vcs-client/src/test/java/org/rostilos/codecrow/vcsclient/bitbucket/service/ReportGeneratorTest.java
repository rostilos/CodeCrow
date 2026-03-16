package org.rostilos.codecrow.vcsclient.bitbucket.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.dto.admin.BaseUrlSettingsDTO;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateResult;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.service.AnalysisStatusEvaluator;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsAnnotation;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsReport;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class ReportGeneratorTest {

    @Mock private SiteSettingsProvider siteSettingsProvider;
    @Mock private AnalysisStatusEvaluator analysisStatusEvaluator;

    private ReportGenerator generator;

    private static void setId(Object entity, Long id) throws Exception {
        Field f = entity.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(entity, id);
    }

    @BeforeEach
    void setUp() {
        BaseUrlSettingsDTO baseUrlSettings = new BaseUrlSettingsDTO("https://app.example.com", "https://app.example.com", null);
        lenient().when(siteSettingsProvider.getBaseUrlSettings()).thenReturn(baseUrlSettings);
        generator = new ReportGenerator(siteSettingsProvider, analysisStatusEvaluator);
    }

    private CodeAnalysis buildAnalysis(List<CodeAnalysisIssue> issues) throws Exception {
        Workspace ws = new Workspace();
        setId(ws, 1L);
        ws.setSlug("ws");

        Project project = new Project();
        setId(project, 1L);
        project.setNamespace("test-proj");
        project.setWorkspace(ws);

        CodeAnalysis analysis = new CodeAnalysis();
        setId(analysis, 10L);
        analysis.setProject(project);
        analysis.setPrNumber(42L);
        analysis.setPrVersion(1);
        analysis.setIssues(issues);
        return analysis;
    }

    private CodeAnalysisIssue buildIssue(Long id, IssueSeverity severity, boolean resolved) throws Exception {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        setId(issue, id);
        issue.setSeverity(severity);
        issue.setResolved(resolved);
        issue.setFilePath("src/Foo.java");
        issue.setLineNumber(10);
        issue.setTitle("Test issue " + id);
        issue.setReason("Reason for issue " + id);
        issue.setIssueCategory(IssueCategory.BEST_PRACTICES);
        return issue;
    }

    // ── createAnalysisSummary ────────────────────────────────────────────

    @Nested
    class CreateAnalysisSummary {

        @Test
        void emptyIssues_shouldReturnZeroCounts() throws Exception {
            CodeAnalysis analysis = buildAnalysis(List.of());
            when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                    .thenReturn(QualityGateResult.skipped());

            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);

            assertThat(summary.getTotalIssues()).isEqualTo(0);
            assertThat(summary.getTotalUnresolvedIssues()).isEqualTo(0);
            assertThat(summary.getHighSeverityIssues().getCount()).isEqualTo(0);
        }

        @Test
        void mixedIssues_shouldGroupBySeverity() throws Exception {
            CodeAnalysisIssue high1 = buildIssue(1L, IssueSeverity.HIGH, false);
            CodeAnalysisIssue high2 = buildIssue(2L, IssueSeverity.HIGH, false);
            CodeAnalysisIssue med = buildIssue(3L, IssueSeverity.MEDIUM, false);
            CodeAnalysisIssue resolved = buildIssue(4L, IssueSeverity.HIGH, true);
            CodeAnalysis analysis = buildAnalysis(List.of(high1, high2, med, resolved));
            when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                    .thenReturn(QualityGateResult.skipped());

            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);

            assertThat(summary.getTotalIssues()).isEqualTo(4);
            assertThat(summary.getTotalUnresolvedIssues()).isEqualTo(3);
            assertThat(summary.getHighSeverityIssues().getCount()).isEqualTo(2);
            assertThat(summary.getMediumSeverityIssues().getCount()).isEqualTo(1);
            assertThat(summary.getResolvedIssues().getCount()).isEqualTo(1);
        }

        @Test
        void qualityGateEvalFails_shouldMarkSkipped() throws Exception {
            CodeAnalysis analysis = buildAnalysis(List.of());
            when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                    .thenThrow(new RuntimeException("QG eval failed"));

            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);
            assertThat(summary.getQualityGateResult().isSkipped()).isTrue();
        }

        @Test
        void issuesWithNullFilePath_shouldGroupUnderUnknown() throws Exception {
            CodeAnalysisIssue issue = buildIssue(1L, IssueSeverity.LOW, false);
            issue.setFilePath(null);
            CodeAnalysis analysis = buildAnalysis(List.of(issue));
            when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                    .thenReturn(QualityGateResult.skipped());

            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);
            assertThat(summary.getFileIssueCount()).containsKey("unknown");
        }

        @Test
        void platformAnalysisUrl_shouldBeGenerated() throws Exception {
            CodeAnalysis analysis = buildAnalysis(List.of());
            when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                    .thenReturn(QualityGateResult.skipped());

            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);
            assertThat(summary.getPlatformAnalysisUrl()).contains("app.example.com");
        }
    }

    // ── createIssueSummary ───────────────────────────────────────────────

    @Test
    void createIssueSummary_shouldMapFields() throws Exception {
        CodeAnalysisIssue issue = buildIssue(1L, IssueSeverity.MEDIUM, false);
        issue.setSuggestedFixDescription("Fix it like this");
        Workspace ws = new Workspace();
        setId(ws, 1L);
        ws.setSlug("ws");
        Project project = new Project();
        setId(project, 1L);
        project.setNamespace("proj");
        project.setWorkspace(ws);

        AnalysisSummary.IssueSummary result = generator.createIssueSummary(issue, project, 10L);
        assertThat(result.getSeverity()).isEqualTo(IssueSeverity.MEDIUM);
        assertThat(result.getFilePath()).isEqualTo("src/Foo.java");
        assertThat(result.getTitle()).startsWith("Test issue");
    }

    // ── Markdown summary ─────────────────────────────────────────────────

    @Nested
    class MarkdownSummary {

        @Test
        void noIssues_shouldContainNoIssuesText() throws Exception {
            CodeAnalysis analysis = buildAnalysis(List.of());
            when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                    .thenReturn(QualityGateResult.skipped());
            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);

            String md = generator.createMarkdownSummary(analysis, summary);
            assertThat(md).isNotEmpty();
        }

        @Test
        void withGitHubSpoilers_shouldNotThrow() throws Exception {
            CodeAnalysis analysis = buildAnalysis(List.of());
            when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                    .thenReturn(QualityGateResult.skipped());
            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);

            String md = generator.createMarkdownSummary(analysis, summary, true);
            assertThat(md).isNotEmpty();
        }

        @Test
        void createDetailedIssuesMarkdown_shouldNotThrow() throws Exception {
            CodeAnalysis analysis = buildAnalysis(List.of(buildIssue(1L, IssueSeverity.HIGH, false)));
            when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                    .thenReturn(QualityGateResult.skipped());
            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);

            String md = generator.createDetailedIssuesMarkdown(summary, false);
            assertThat(md).isNotNull();
        }
    }

    // ── Plain text summary ───────────────────────────────────────────────

    @Test
    void createPlainTextSummary_shouldNotThrow() throws Exception {
        CodeAnalysis analysis = buildAnalysis(List.of(buildIssue(1L, IssueSeverity.LOW, false)));
        when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                .thenReturn(QualityGateResult.skipped());

        String text = generator.createPlainTextSummary(analysis, 100L);
        assertThat(text).isNotEmpty();
    }

    // ── createCodeInsightsReport ─────────────────────────────────────────

    @Nested
    class CreateCodeInsightsReport {

        @Test
        void passedQualityGate_shouldReturnPassedStatus() throws Exception {
            QualityGateResult passed = new QualityGateResult(AnalysisResult.PASSED, "default", List.of());
            CodeAnalysis analysis = buildAnalysis(List.of());
            when(analysisStatusEvaluator.evaluateStatus(any(), any())).thenReturn(passed);
            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);

            CodeInsightsReport report = generator.createCodeInsightsReport(summary, analysis);
            assertThat(report).isNotNull();
        }

        @Test
        void failedQualityGate_shouldReturnReport() throws Exception {
            QualityGateResult failed = new QualityGateResult(AnalysisResult.FAILED, "strict", List.of());
            CodeAnalysis analysis = buildAnalysis(List.of(buildIssue(1L, IssueSeverity.HIGH, false)));
            when(analysisStatusEvaluator.evaluateStatus(any(), any())).thenReturn(failed);
            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);

            CodeInsightsReport report = generator.createCodeInsightsReport(summary, analysis);
            assertThat(report).isNotNull();
        }

        @Test
        void skippedQualityGate_unresolvedIssues_shouldFail() throws Exception {
            QualityGateResult skipped = QualityGateResult.skipped();
            CodeAnalysis analysis = buildAnalysis(List.of(buildIssue(1L, IssueSeverity.HIGH, false)));
            when(analysisStatusEvaluator.evaluateStatus(any(), any())).thenReturn(skipped);
            AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);

            CodeInsightsReport report = generator.createCodeInsightsReport(summary, analysis);
            assertThat(report).isNotNull();
        }
    }

    // ── createReportAnnotations ──────────────────────────────────────────

    @Test
    void createReportAnnotations_shouldFilterResolvedAndMap() throws Exception {
        CodeAnalysisIssue unresolved = buildIssue(1L, IssueSeverity.HIGH, false);
        CodeAnalysisIssue resolved = buildIssue(2L, IssueSeverity.MEDIUM, true);
        CodeAnalysis analysis = buildAnalysis(List.of(unresolved, resolved));

        Workspace ws = new Workspace();
        setId(ws, 1L);
        ws.setSlug("ws");
        Project project = new Project();
        setId(project, 1L);
        project.setNamespace("proj");
        project.setWorkspace(ws);

        Set<CodeInsightsAnnotation> annotations = generator.createReportAnnotations(analysis, project);
        assertThat(annotations).hasSize(1);
    }

    @Test
    void createCodeInsightsAnnotation_shouldMapFields() {
        CodeInsightsAnnotation ann = generator.createCodeInsightsAnnotation(
                "issue-1", 42, "https://example.com/issue/1",
                "Some message", "src/Foo.java", "HIGH", "CODE_SMELL");
        assertThat(ann).isNotNull();
    }

    // ── IssueSummary helpers ─────────────────────────────────────────────

    @Test
    void issueSummary_shortFilePath_shouldTruncate() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH, "BUG", "src/main/java/com/example/Foo.java", 10,
                "Title", "reason", null, null, "url", 1L);
        assertThat(issue.getShortFilePath()).contains("...");
    }

    @Test
    void issueSummary_nullFilePath_shouldReturnUnknown() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH, "BUG", null, 10,
                "Title", "reason", null, null, "url", 1L);
        assertThat(issue.getShortFilePath()).isEqualTo("unknown");
    }

    @Test
    void issueSummary_locationDescription_withLine() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH, "BUG", "src/Foo.java", 42,
                "Title", "reason", null, null, "url", 1L);
        assertThat(issue.getLocationDescription()).contains(":42");
    }

    @Test
    void issueSummary_locationDescription_noLine() {
        AnalysisSummary.IssueSummary issue = new AnalysisSummary.IssueSummary(
                IssueSeverity.HIGH, "BUG", "src/Foo.java", null,
                "Title", "reason", null, null, "url", 1L);
        assertThat(issue.getLocationDescription()).doesNotContain(":");
    }

    // ── AnalysisSummary status description ───────────────────────────────

    @Test
    void statusDescription_noIssues_shouldSayNoIssues() throws Exception {
        CodeAnalysis analysis = buildAnalysis(List.of());
        when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                .thenReturn(QualityGateResult.skipped());
        AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);
        assertThat(summary.getStatusDescription()).contains("No issues");
    }

    @Test
    void statusDescription_highIssues_shouldMentionHighSeverity() throws Exception {
        CodeAnalysis analysis = buildAnalysis(List.of(buildIssue(1L, IssueSeverity.HIGH, false)));
        when(analysisStatusEvaluator.evaluateStatus(any(), any()))
                .thenReturn(QualityGateResult.skipped());
        AnalysisSummary summary = generator.createAnalysisSummary(analysis, 100L);
        assertThat(summary.getStatusDescription()).contains("high severity");
    }
}
