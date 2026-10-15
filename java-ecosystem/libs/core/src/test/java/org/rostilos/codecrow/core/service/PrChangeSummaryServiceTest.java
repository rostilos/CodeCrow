package org.rostilos.codecrow.core.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.PrChangeSummary;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.PrChangeSummaryRepository;
import java.util.Map;
import java.util.List;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class PrChangeSummaryServiceTest {
    final PrChangeSummaryRepository repository = mock(PrChangeSummaryRepository.class);
    final ObjectMapper mapper = new ObjectMapper();
    final PrChangeSummaryService service = new PrChangeSummaryService(repository, mapper);

    CodeAnalysis analysis(long projectId, long id, String revision) {
        Project project = mock(Project.class); when(project.getId()).thenReturn(projectId);
        CodeAnalysis analysis = mock(CodeAnalysis.class);
        when(analysis.getId()).thenReturn(id); when(analysis.getProject()).thenReturn(project);
        when(analysis.getPrNumber()).thenReturn(9L); when(analysis.getCommitHash()).thenReturn(revision);
        return analysis;
    }
    Map<String,Object> payload(long projectId, String revision) {
        return Map.of("projectId", projectId, "pullRequestId", 9L, "sourceRevision", revision,
            "summary", "Added empty input handling", "files", List.of(Map.of("path", "a.py")),
            "relationships", List.of(), "status", "complete");
    }
    @Test void persistsAndLoadsExactProjectPrRevision() throws Exception {
        var analysis = analysis(7L, 101L, "abc");
        service.persist(analysis, payload(7L, "abc"));
        var captor = org.mockito.ArgumentCaptor.forClass(PrChangeSummary.class);
        verify(repository).save(captor.capture());
        var row = captor.getValue();
        assertThat(row.getAnalysisId()).isEqualTo(101L);
        assertThat(row.getProjectId()).isEqualTo(7L);
        when(repository.findFirstByProjectIdAndPrNumberAndSourceRevisionOrderByCreatedAtDesc(7L, 9L, "abc"))
            .thenReturn(Optional.of(row));
        assertThat(service.find(7L, 9L, "abc")).containsEntry("summary", "Added empty input handling");
        assertThat(service.find(8L, 9L, "abc")).isNull();
        assertThat(service.find(7L, 9L, "new")).isNull();
    }
    @Test void rejectsCrossTenantOrStalePayloadAndCacheCopy() {
        var own = analysis(7L, 101L, "abc");
        service.persist(own, payload(8L, "abc"));
        service.persist(own, payload(7L, "old"));
        service.copyForAnalysis(own, analysis(8L, 102L, "abc"));
        service.copyForAnalysis(own, analysis(7L, 102L, "new"));
        verifyNoInteractions(repository);
    }
    @Test void databaseFailureDoesNotFailReviewOrQaFallback() {
        when(repository.save(any())).thenThrow(new IllegalStateException("unavailable"));
        assertThatCode(() -> service.persist(analysis(7L, 101L, "abc"), payload(7L, "abc"))).doesNotThrowAnyException();
        when(repository.findFirstByProjectIdAndPrNumberAndSourceRevisionOrderByCreatedAtDesc(7L, 9L, "abc"))
            .thenThrow(new IllegalStateException("unavailable"));
        assertThat(service.find(7L, 9L, "abc")).isNull();
    }
    @Test void malformedSavedContextAllowsSourceFallback() throws Exception {
        for (var payload : List.of(Map.of("projectId", 7L), payload(8L, "abc"))) {
            var row = new PrChangeSummary(analysis(7L, 101L, "abc"), mapper.writeValueAsString(payload));
            when(repository.findFirstByProjectIdAndPrNumberAndSourceRevisionOrderByCreatedAtDesc(7L, 9L, "abc"))
                .thenReturn(Optional.of(row));
            assertThat(service.find(7L, 9L, "abc")).isNull();
        }
    }
}
