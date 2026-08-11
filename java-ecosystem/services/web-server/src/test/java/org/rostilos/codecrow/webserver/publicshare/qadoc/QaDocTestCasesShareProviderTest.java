package org.rostilos.codecrow.webserver.publicshare.qadoc;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.QaDocDocumentService;

import java.util.Arrays;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class QaDocTestCasesShareProviderTest {

    @Test
    void returnsOnlyTheSanitizedTestCaseContract() {
        QaDocDocumentService documents = mock(QaDocDocumentService.class);
        CodeAnalysisService analyses = mock(CodeAnalysisService.class);
        Project project = mock(Project.class);
        when(project.getId()).thenReturn(12L);
        when(project.getName()).thenReturn("Acme Checkout");

        QaDocDocument document = new QaDocDocument(project, 17L);
        document.setId(88L);
        document.setTaskId("SHOP-42");
        document.setLastAnalysisId(71L);
        document.setCommitHash("0123456789012345678901234567890123456789");
        document.setMarkdownContent("""
                # Internal overview
                <!-- codecrow-test-cases:start -->
                ### Test Scenarios
                **Save the form** (HIGH)
                - **Expected Result:** The confirmation appears
                <!-- codecrow-test-cases:end -->
                """);
        CodeAnalysis analysis = mock(CodeAnalysis.class);
        when(analysis.getProject()).thenReturn(project);
        when(analysis.getTaskId()).thenReturn("SHOP-42");
        when(analysis.getTaskSummary()).thenReturn("Add a saved-card checkout flow");
        when(documents.findDocumentById(88L)).thenReturn(Optional.of(document));
        when(analyses.findById(71L)).thenReturn(Optional.of(analysis));
        QaDocTestCasesShareProvider provider = new QaDocTestCasesShareProvider(documents, analyses);

        QaDocTestCasesPublicPreview preview = provider.getPublicPreview("88").orElseThrow();

        assertThat(preview.title()).isEqualTo("QA test cases");
        assertThat(preview.projectName()).isEqualTo("Acme Checkout");
        assertThat(preview.taskKey()).isEqualTo("SHOP-42");
        assertThat(preview.taskSummary()).isEqualTo("Add a saved-card checkout flow");
        assertThat(preview.testCases()).singleElement()
                .satisfies(testCase -> assertThat(testCase.title()).isEqualTo("Save the form"));
        assertThat(Arrays.stream(QaDocTestCasesPublicPreview.class.getRecordComponents())
                .map(component -> component.getName()))
                .containsExactly("title", "projectName", "taskKey", "taskSummary", "testCases")
                .doesNotContain("id", "project", "workspace", "prNumber", "taskId", "commitHash");
    }

    @Test
    void neverFallsBackToSharingAnUnmarkedLegacyDocument() {
        QaDocDocumentService documents = mock(QaDocDocumentService.class);
        CodeAnalysisService analyses = mock(CodeAnalysisService.class);
        QaDocDocument document = new QaDocDocument(null, 17L);
        document.setId(89L);
        document.setMarkdownContent("""
                # Secret project overview
                Workspace: Acme
                **A legacy test** (HIGH)
                - **Expected Result:** It works
        """);
        when(documents.findDocumentById(89L)).thenReturn(Optional.of(document));
        QaDocTestCasesShareProvider provider = new QaDocTestCasesShareProvider(documents, analyses);

        assertThat(provider.getPublicPreview("89")).isEmpty();
    }

    @Test
    void doesNotExposeTaskSummaryFromAnotherProject() {
        QaDocDocumentService documents = mock(QaDocDocumentService.class);
        CodeAnalysisService analyses = mock(CodeAnalysisService.class);
        Project documentProject = mock(Project.class);
        Project analysisProject = mock(Project.class);
        when(documentProject.getId()).thenReturn(12L);
        when(documentProject.getName()).thenReturn("Checkout");
        when(analysisProject.getId()).thenReturn(99L);

        QaDocDocument document = new QaDocDocument(documentProject, 17L);
        document.setId(90L);
        document.setTaskId("SHOP-42");
        document.setLastAnalysisId(72L);
        document.setMarkdownContent("""
                <!-- codecrow-test-cases:start -->
                ### Test Scenarios
                **Save the form** (HIGH)
                - **Expected Result:** The confirmation appears
                <!-- codecrow-test-cases:end -->
                """);
        CodeAnalysis analysis = mock(CodeAnalysis.class);
        when(analysis.getProject()).thenReturn(analysisProject);
        when(analysis.getTaskId()).thenReturn("SHOP-42");
        when(analysis.getTaskSummary()).thenReturn("Secret from another project");
        when(documents.findDocumentById(90L)).thenReturn(Optional.of(document));
        when(analyses.findById(72L)).thenReturn(Optional.of(analysis));

        QaDocTestCasesPublicPreview preview = new QaDocTestCasesShareProvider(documents, analyses)
                .getPublicPreview("90")
                .orElseThrow();

        assertThat(preview.projectName()).isEqualTo("Checkout");
        assertThat(preview.taskKey()).isEqualTo("SHOP-42");
        assertThat(preview.taskSummary()).isNull();
    }
}
