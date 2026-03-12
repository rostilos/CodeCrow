package org.rostilos.codecrow.commitgraph.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Captor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.commitgraph.model.AnalyzedCommit;
import org.rostilos.codecrow.commitgraph.persistence.AnalyzedCommitRepository;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class AnalyzedCommitServiceTest {

    @Mock
    private AnalyzedCommitRepository repository;

    @Captor
    private ArgumentCaptor<List<AnalyzedCommit>> commitListCaptor;

    private AnalyzedCommitService service;

    @BeforeEach
    void setUp() {
        service = new AnalyzedCommitService(repository);
    }

    private static void setId(Object entity, Long id) throws Exception {
        Field f = entity.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(entity, id);
    }

    // ── recordBranchCommitsAnalyzed ──────────────────────────────────────

    @Test
    void recordBranchCommitsAnalyzed_withNullHashes_shouldDoNothing() {
        Project project = new Project();
        service.recordBranchCommitsAnalyzed(project, null);
        verifyNoInteractions(repository);
    }

    @Test
    void recordBranchCommitsAnalyzed_withEmptyHashes_shouldDoNothing() {
        Project project = new Project();
        service.recordBranchCommitsAnalyzed(project, List.of());
        verifyNoInteractions(repository);
    }

    @Test
    void recordBranchCommitsAnalyzed_allNew_shouldSaveAll() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        when(repository.findAnalyzedHashesByProjectIdAndCommitHashIn(eq(1L), anyList()))
                .thenReturn(Set.of());

        service.recordBranchCommitsAnalyzed(project, List.of("aaa", "bbb", "ccc"));

        verify(repository).saveAll(commitListCaptor.capture());
        List<AnalyzedCommit> saved = commitListCaptor.getValue();
        assertThat(saved).hasSize(3);
        assertThat(saved).allMatch(c -> c.getAnalysisType() == AnalysisType.BRANCH_ANALYSIS);
    }

    @Test
    void recordBranchCommitsAnalyzed_someAlreadyAnalyzed_shouldSaveOnlyNew() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        when(repository.findAnalyzedHashesByProjectIdAndCommitHashIn(eq(1L), anyList()))
                .thenReturn(Set.of("aaa"));

        service.recordBranchCommitsAnalyzed(project, List.of("aaa", "bbb"));

        verify(repository).saveAll(commitListCaptor.capture());
        List<AnalyzedCommit> saved = commitListCaptor.getValue();
        assertThat(saved).hasSize(1);
        assertThat(saved.get(0).getCommitHash()).isEqualTo("bbb");
    }

    @Test
    void recordBranchCommitsAnalyzed_allAlreadyAnalyzed_shouldNotSave() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        when(repository.findAnalyzedHashesByProjectIdAndCommitHashIn(eq(1L), anyList()))
                .thenReturn(Set.of("aaa", "bbb"));

        service.recordBranchCommitsAnalyzed(project, List.of("aaa", "bbb"));

        verify(repository, never()).saveAll(anyList());
    }

    // ── recordPrCommitsAnalyzed ──────────────────────────────────────────

    @Test
    void recordPrCommitsAnalyzed_withNullHashes_shouldDoNothing() {
        service.recordPrCommitsAnalyzed(new Project(), null, null);
        verifyNoInteractions(repository);
    }

    @Test
    void recordPrCommitsAnalyzed_withEmptyHashes_shouldDoNothing() {
        service.recordPrCommitsAnalyzed(new Project(), List.of(), null);
        verifyNoInteractions(repository);
    }

    @Test
    void recordPrCommitsAnalyzed_withAnalysis_shouldSetAnalysisId() throws Exception {
        Project project = new Project();
        setId(project, 2L);
        CodeAnalysis analysis = new CodeAnalysis();
        setId(analysis, 99L);

        when(repository.findAnalyzedHashesByProjectIdAndCommitHashIn(eq(2L), anyList()))
                .thenReturn(Set.of());

        service.recordPrCommitsAnalyzed(project, List.of("xyz"), analysis);

        verify(repository).saveAll(commitListCaptor.capture());
        List<AnalyzedCommit> saved = commitListCaptor.getValue();
        assertThat(saved).hasSize(1);
        assertThat(saved.get(0).getAnalysisId()).isEqualTo(99L);
        assertThat(saved.get(0).getAnalysisType()).isEqualTo(AnalysisType.PR_REVIEW);
    }

    @Test
    void recordPrCommitsAnalyzed_withNullAnalysis_shouldSetNullAnalysisId() throws Exception {
        Project project = new Project();
        setId(project, 2L);

        when(repository.findAnalyzedHashesByProjectIdAndCommitHashIn(eq(2L), anyList()))
                .thenReturn(Set.of());

        service.recordPrCommitsAnalyzed(project, List.of("xyz"), null);

        verify(repository).saveAll(commitListCaptor.capture());
        assertThat(commitListCaptor.getValue().get(0).getAnalysisId()).isNull();
    }

    // ── filterUnanalyzed ─────────────────────────────────────────────────

    @Test
    void filterUnanalyzed_withNullHashes_shouldReturnEmpty() {
        List<String> result = service.filterUnanalyzed(1L, null);
        assertThat(result).isEmpty();
    }

    @Test
    void filterUnanalyzed_withEmptyHashes_shouldReturnEmpty() {
        List<String> result = service.filterUnanalyzed(1L, List.of());
        assertThat(result).isEmpty();
    }

    @Test
    void filterUnanalyzed_shouldReturnOnlyUnanalyzed() {
        when(repository.findAnalyzedHashesByProjectIdAndCommitHashIn(eq(1L), anyList()))
                .thenReturn(Set.of("aaa", "ccc"));

        List<String> result = service.filterUnanalyzed(1L, List.of("aaa", "bbb", "ccc", "ddd"));

        assertThat(result).containsExactly("bbb", "ddd");
    }

    @Test
    void filterUnanalyzed_noneAnalyzed_shouldReturnAll() {
        when(repository.findAnalyzedHashesByProjectIdAndCommitHashIn(eq(1L), anyList()))
                .thenReturn(Set.of());

        List<String> result = service.filterUnanalyzed(1L, List.of("aaa", "bbb"));

        assertThat(result).containsExactly("aaa", "bbb");
    }

    // ── isAnalyzed ───────────────────────────────────────────────────────

    @Test
    void isAnalyzed_whenExists_shouldReturnTrue() {
        when(repository.existsByProjectIdAndCommitHash(1L, "abc")).thenReturn(true);

        assertThat(service.isAnalyzed(1L, "abc")).isTrue();
    }

    @Test
    void isAnalyzed_whenNotExists_shouldReturnFalse() {
        when(repository.existsByProjectIdAndCommitHash(1L, "abc")).thenReturn(false);

        assertThat(service.isAnalyzed(1L, "abc")).isFalse();
    }
}
