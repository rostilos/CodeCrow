package org.rostilos.codecrow.webserver.analysis.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.persistence.repository.qadoc.QaDocDocumentRepository;
import org.springframework.web.server.ResponseStatusException;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class QaDocProgressServiceTest {
    private final QaDocDocumentRepository repository = mock(QaDocDocumentRepository.class);
    private final QaDocProgressService service = new QaDocProgressService(repository);
    private final QaDocDocument document = new QaDocDocument();

    private String setup() {
        document.replaceContent(null, 1L, "head", """
                <!-- codecrow-test-cases:start -->
                ## Test scenarios
                <!-- codecrow-test-cases:content -->
                ### Login
                **Sign in** (HIGH)
                - **Expected:** dashboard
                <!-- codecrow-test-cases:end -->
                """);
        when(repository.findForUpdate(10L, 7L)).thenReturn(Optional.of(document));
        when(repository.save(document)).thenReturn(document);
        return QaDocProgressService.revision(document);
    }

    @Test
    void checksAreIndependentPersistedAndIdempotent() {
        String revision = setup();
        service.update(10L, 7L, revision, "develop", 0, true, "check");
        var result = service.update(10L, 7L, revision, "develop", 0, true, "check");
        assertThat(result.environments().get("develop")).containsExactly(0);
        assertThat(result.environments().get("stage")).isEmpty();
        assertThat(result.environments().get("production")).isEmpty();
        verify(repository, times(2)).save(document);
        when(repository.findByProjectIdAndPrNumber(10L, 7L)).thenReturn(Optional.of(document));
        assertThat(service.get(10L, 7L).environments().get("develop")).containsExactly(0);
        assertThat(service.update(10L, 7L, revision, "develop", 0, false, "check").environments().get("develop")).isEmpty();
    }

    @Test
    void environmentsCanBeCustomizedWithoutRecreatingDeletedDefaults() {
        String revision = setup();
        service.update(10L, 7L, revision, " staging-eu ", null, null, "add");
        service.update(10L, 7L, revision, "production", null, null, "delete");
        document.replaceContent(null, 2L, "next-head", "new scenarios");
        assertThat(document.getEnvironmentChecks()).containsKeys("staging-eu", "stage", "develop").doesNotContainKey("production");
        assertThatThrownBy(() -> service.update(10L, 7L, QaDocProgressService.revision(document), "Stage", null, null, "add"))
                .isInstanceOf(ResponseStatusException.class);
    }

    @Test
    void regenerationClearsChecksAndRejectsStaleAndInvalidUpdates() {
        String revision = setup();
        service.update(10L, 7L, revision, "stage", 0, true, "check");
        document.replaceContent(null, 2L, "head", document.getMarkdownContent());
        assertThat(document.getEnvironmentChecks().get("stage")).containsExactly(0);
        document.replaceContent(null, 3L, "next-head", document.getMarkdownContent());
        assertThat(document.getEnvironmentChecks().get("stage")).isEmpty();
        assertThatThrownBy(() -> service.update(10L, 7L, revision, "stage", 0, true, "check"))
                .isInstanceOfSatisfying(ResponseStatusException.class, e -> assertThat(e.getStatusCode().value()).isEqualTo(409));
        assertThatThrownBy(() -> service.update(10L, 7L, QaDocProgressService.revision(document), "stage", 8, true, "check"))
                .isInstanceOfSatisfying(ResponseStatusException.class, e -> assertThat(e.getStatusCode().value()).isEqualTo(400));
    }

    @Test
    void cannotReadOrMutateAnotherProjectsDocument() {
        assertThatThrownBy(() -> service.get(11L, 7L)).isInstanceOf(ResponseStatusException.class);
        assertThatThrownBy(() -> service.update(11L, 7L, "revision", "develop", 0, true, "check"))
                .isInstanceOf(ResponseStatusException.class);
        verify(repository, never()).save(any());
    }
}
