package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.persistence.repository.qadoc.QaDocDocumentRepository;

import java.lang.reflect.Field;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("QaDocDocumentService")
class QaDocDocumentServiceTest {

    @Mock
    private QaDocDocumentRepository qaDocDocumentRepository;

    private QaDocDocumentService service;

    @BeforeEach
    void setUp() {
        service = new QaDocDocumentService(qaDocDocumentRepository);
    }

    @Test
    @DisplayName("upserts latest document for project PR")
    void upsertsLatestDocument() {
        Project project = project(42L);
        when(qaDocDocumentRepository.findByProjectIdAndPrNumber(42L, 7L))
                .thenReturn(Optional.empty());
        when(qaDocDocumentRepository.save(any(QaDocDocument.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        service.upsertLatestDocument(project, 7L, "PROJ-123", 100L, "abc123", "## QA Doc");

        ArgumentCaptor<QaDocDocument> captor = ArgumentCaptor.forClass(QaDocDocument.class);
        verify(qaDocDocumentRepository).save(captor.capture());
        QaDocDocument saved = captor.getValue();
        assertThat(saved.getProject()).isEqualTo(project);
        assertThat(saved.getPrNumber()).isEqualTo(7L);
        assertThat(saved.getTaskId()).isEqualTo("PROJ-123");
        assertThat(saved.getLastAnalysisId()).isEqualTo(100L);
        assertThat(saved.getCommitHash()).isEqualTo("abc123");
        assertThat(saved.getMarkdownContent()).isEqualTo("## QA Doc");
    }

    @Test
    @DisplayName("updates existing document instead of creating a second row")
    void updatesExistingDocument() {
        Project project = project(42L);
        QaDocDocument existing = new QaDocDocument(project, 7L);
        existing.replaceContent("PROJ-122", 99L, "old123", "old doc");

        when(qaDocDocumentRepository.findByProjectIdAndPrNumber(42L, 7L))
                .thenReturn(Optional.of(existing));
        when(qaDocDocumentRepository.save(any(QaDocDocument.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        QaDocDocument updated = service.upsertLatestDocument(
                project, 7L, "PROJ-123", 100L, "abc123", "new doc");

        assertThat(updated).isSameAs(existing);
        assertThat(updated.getTaskId()).isEqualTo("PROJ-123");
        assertThat(updated.getLastAnalysisId()).isEqualTo(100L);
        assertThat(updated.getCommitHash()).isEqualTo("abc123");
        assertThat(updated.getMarkdownContent()).isEqualTo("new doc");
    }

    @Test
    @DisplayName("rejects blank markdown")
    void rejectsBlankMarkdown() {
        Project project = project(42L);

        assertThatThrownBy(() ->
                service.upsertLatestDocument(project, 7L, "PROJ-123", null, null, " "))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Markdown content");
    }

    private static Project project(Long id) {
        Project project = new Project();
        setField(project, "id", id);
        return project;
    }

    private static void setField(Object target, String fieldName, Object value) {
        try {
            Field field = target.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(target, value);
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException(e);
        }
    }
}
