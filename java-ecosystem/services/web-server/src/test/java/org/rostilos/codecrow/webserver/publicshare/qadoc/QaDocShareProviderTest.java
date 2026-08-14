package org.rostilos.codecrow.webserver.publicshare.qadoc;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.QaDocDocumentService;
import org.rostilos.codecrow.security.service.UserDetailsImpl;
import org.rostilos.codecrow.security.web.WorkspaceSecurity;
import org.springframework.security.core.Authentication;

import java.util.Arrays;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class QaDocShareProviderTest {

    @Test
    void returnsTheExplicitlyShareableDocumentTabs() {
        QaDocDocumentService documents = mock(QaDocDocumentService.class);
        CodeAnalysisService analyses = mock(CodeAnalysisService.class);
        WorkspaceSecurity workspaceSecurity = mock(WorkspaceSecurity.class);
        Project project = mock(Project.class);
        when(project.getId()).thenReturn(12L);
        when(project.getName()).thenReturn("Acme Checkout");

        QaDocDocument document = new QaDocDocument(project, 17L);
        document.setId(88L);
        document.setTaskId("SHOP-42");
        document.setLastAnalysisId(71L);
        document.setCommitHash("0123456789012345678901234567890123456789");
        document.setMarkdownContent("""
                # QA Testing Guide — Saved cards
                ## 1. What Changed
                Customers can reuse a saved card.

                <!-- codecrow-test-cases:start -->
                ### Test Scenarios
                <!-- codecrow-test-cases:content -->
                **Save the form** (HIGH)
                - **Expected Result:** The confirmation appears
                <!-- codecrow-test-cases:end -->

                <!-- codecrow-environment:start -->
                ## 6. Setup and Environment Notes
                <!-- codecrow-environment:content -->
                - Enable saved cards in the QA environment.
                <!-- codecrow-environment:end -->
                """);
        CodeAnalysis analysis = mock(CodeAnalysis.class);
        when(analysis.getProject()).thenReturn(project);
        when(analysis.getTaskId()).thenReturn("SHOP-42");
        when(analysis.getTaskSummary()).thenReturn("Add a saved-card checkout flow");
        when(documents.findDocumentById(88L)).thenReturn(Optional.of(document));
        when(analyses.findById(71L)).thenReturn(Optional.of(analysis));
        QaDocShareProvider provider = new QaDocShareProvider(
                documents, analyses, workspaceSecurity);

        QaDocPublicPreview preview = provider.getPublicPreview("88").orElseThrow();

        assertThat(preview.title()).isEqualTo("QA documentation");
        assertThat(preview.projectName()).isEqualTo("Acme Checkout");
        assertThat(preview.taskKey()).isEqualTo("SHOP-42");
        assertThat(preview.taskSummary()).isEqualTo("Add a saved-card checkout flow");
        assertThat(preview.overviewMarkdown())
                .contains("What Changed", "Customers can reuse a saved card")
                .doesNotContain("Save the form", "Setup and Environment Notes");
        assertThat(preview.testCases()).singleElement()
                .satisfies(testCase -> assertThat(testCase.title()).isEqualTo("Save the form"));
        assertThat(preview.environmentMarkdown())
                .isEqualTo("- Enable saved cards in the QA environment.");
        assertThat(Arrays.stream(QaDocPublicPreview.class.getRecordComponents())
                .map(component -> component.getName()))
                .containsExactly(
                        "title", "projectName", "taskKey", "taskSummary",
                        "overviewMarkdown", "testCases", "environmentMarkdown")
                .doesNotContain("id", "project", "workspace", "prNumber", "taskId", "commitHash");
    }

    @Test
    void neverFallsBackToSharingAnUnmarkedLegacyDocument() {
        QaDocDocumentService documents = mock(QaDocDocumentService.class);
        CodeAnalysisService analyses = mock(CodeAnalysisService.class);
        WorkspaceSecurity workspaceSecurity = mock(WorkspaceSecurity.class);
        QaDocDocument document = new QaDocDocument(null, 17L);
        document.setId(89L);
        document.setMarkdownContent("""
                # Secret project overview
                Workspace: Acme
                **A legacy test** (HIGH)
                - **Expected Result:** It works
        """);
        when(documents.findDocumentById(89L)).thenReturn(Optional.of(document));
        QaDocShareProvider provider = new QaDocShareProvider(
                documents, analyses, workspaceSecurity);

        assertThat(provider.getPublicPreview("89")).isEmpty();
    }

    @Test
    void doesNotExposeTaskSummaryFromAnotherProject() {
        QaDocDocumentService documents = mock(QaDocDocumentService.class);
        CodeAnalysisService analyses = mock(CodeAnalysisService.class);
        WorkspaceSecurity workspaceSecurity = mock(WorkspaceSecurity.class);
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
                <!-- codecrow-test-cases:content -->
                **Save the form** (HIGH)
                - **Expected Result:** The confirmation appears
                <!-- codecrow-test-cases:end -->

                <!-- codecrow-environment:start -->
                ### Environment
                <!-- codecrow-environment:content -->
                No special setup is required.
                <!-- codecrow-environment:end -->
                """);
        CodeAnalysis analysis = mock(CodeAnalysis.class);
        when(analysis.getProject()).thenReturn(analysisProject);
        when(analysis.getTaskId()).thenReturn("SHOP-42");
        when(analysis.getTaskSummary()).thenReturn("Secret from another project");
        when(documents.findDocumentById(90L)).thenReturn(Optional.of(document));
        when(analyses.findById(72L)).thenReturn(Optional.of(analysis));

        QaDocPublicPreview preview = new QaDocShareProvider(
                documents, analyses, workspaceSecurity)
                .getPublicPreview("90")
                .orElseThrow();

        assertThat(preview.projectName()).isEqualTo("Checkout");
        assertThat(preview.taskKey()).isEqualTo("SHOP-42");
        assertThat(preview.taskSummary()).isNull();
    }

    @Test
    void returnsTheRealQaDocRouteOnlyForAnAuthorizedWorkspaceMember() {
        QaDocDocumentService documents = mock(QaDocDocumentService.class);
        CodeAnalysisService analyses = mock(CodeAnalysisService.class);
        WorkspaceSecurity workspaceSecurity = mock(WorkspaceSecurity.class);
        Project project = mock(Project.class);
        Workspace workspace = mock(Workspace.class);
        Authentication authentication = mock(Authentication.class);
        UserDetailsImpl principal = mock(UserDetailsImpl.class);
        when(authentication.isAuthenticated()).thenReturn(true);
        when(authentication.getPrincipal()).thenReturn(principal);
        when(project.getId()).thenReturn(12L);
        when(project.getNamespace()).thenReturn("checkout-service");
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getSlug()).thenReturn("acme");
        when(workspaceSecurity.isProjectWorkspaceMember(12L, authentication)).thenReturn(true);

        QaDocDocument document = new QaDocDocument(project, 524L);
        document.setId(91L);
        when(documents.findDocumentById(91L)).thenReturn(Optional.of(document));
        QaDocShareProvider provider = new QaDocShareProvider(
                documents, analyses, workspaceSecurity);

        assertThat(provider.getAuthorizedPath("91", authentication))
                .contains("/dashboard/acme/projects/checkout-service?prNumber=524&subTab=qa-doc");

        when(workspaceSecurity.isProjectWorkspaceMember(12L, authentication)).thenReturn(false);
        assertThat(provider.getAuthorizedPath("91", authentication)).isEmpty();
        assertThat(provider.getAuthorizedPath("91", null)).isEmpty();
    }
}
