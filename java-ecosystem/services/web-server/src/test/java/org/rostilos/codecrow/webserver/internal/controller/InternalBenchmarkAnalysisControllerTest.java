package org.rostilos.codecrow.webserver.internal.controller;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class InternalBenchmarkAnalysisControllerTest {

    @Mock
    private CodeAnalysisService codeAnalysisService;

    private InternalBenchmarkAnalysisController controller;

    @BeforeEach
    void setUp() {
        controller = new InternalBenchmarkAnalysisController(
                codeAnalysisService);
    }

    @Test
    void returnsOnlyTransientProductFinalization() {
        Map<String, Object> analysisData = Map.of(
                "comment", "Review complete",
                "issues", List.of(Map.of(
                        "severity", "HIGH",
                        "file", "Model.php",
                        "line", 2,
                        "reason", "Avoid the dangerous call")));
        Map<String, String> fileContents = Map.of(
                "Model.php",
                "<?php\ndanger();\n");

        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        issue.setSeverity(IssueSeverity.HIGH);
        issue.setIssueCategory(IssueCategory.SECURITY);
        issue.setFilePath("Model.php");
        issue.setLineNumber(2);
        issue.setTitle("Dangerous call");
        issue.setReason("Avoid the dangerous call");
        issue.setCodeSnippet("danger();");
        when(codeAnalysisService.finalizeIssuesWithoutPersistence(
                analysisData,
                fileContents)).thenReturn(List.of(issue));

        Map<String, Object> body = controller.finalizeBenchmarkAnalysis(
                        new InternalBenchmarkAnalysisController
                                .BenchmarkFinalizationRequest(
                                analysisData,
                                fileContents))
                .getBody();

        assertThat(body)
                .containsEntry(
                        "kind",
                        "codecrow-isolated-analysis-finalization")
                .containsEntry("rawIssueCount", 1)
                .containsEntry("finalIssueCount", 1)
                .containsEntry("analysisDataValidated", true)
                .containsEntry("persisted", false)
                .containsEntry("published", false)
                .containsEntry("previousIssueStateUsed", false);
        assertThat((List<?>) body.get("issues")).singleElement()
                .asInstanceOf(
                        org.assertj.core.api.InstanceOfAssertFactories.MAP)
                .containsEntry("file", "Model.php")
                .containsEntry("line", 2)
                .containsEntry("severity", "HIGH")
                .containsEntry("category", "SECURITY");
    }

    @Test
    void rejectsMalformedAnalysisBeforeFinalization() {
        Map<String, Object> malformed = Map.of("comment", "missing issues");

        assertThatThrownBy(() -> controller.finalizeBenchmarkAnalysis(
                new InternalBenchmarkAnalysisController
                        .BenchmarkFinalizationRequest(
                        malformed,
                        Map.of())))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining(
                        "Analysis data missing required fields");

        verify(codeAnalysisService, never())
                .finalizeIssuesWithoutPersistence(
                        malformed,
                        Map.of());
    }

    @Test
    void rejectsNonCollectionIssuesBeforeFinalization() {
        Map<String, Object> malformed = Map.of(
                "comment",
                "done",
                "issues",
                "not-an-issue-collection");

        assertThatThrownBy(() -> controller.finalizeBenchmarkAnalysis(
                new InternalBenchmarkAnalysisController
                        .BenchmarkFinalizationRequest(
                        malformed,
                        Map.of())))
                .isInstanceOf(ResponseStatusException.class)
                .hasMessageContaining(
                        "'issues' must be an array or object");

        verify(codeAnalysisService, never())
                .finalizeIssuesWithoutPersistence(
                        malformed,
                        Map.of());
    }
}
