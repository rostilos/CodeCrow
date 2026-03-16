package org.rostilos.codecrow.analysisengine.service.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class BranchHealthServiceTest {

    @Mock private BranchRepository branchRepository;
    @Mock private AnalyzedCommitService analyzedCommitService;

    private BranchHealthService service;

    private static void setId(Object entity, Long id) throws Exception {
        Field f = entity.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(entity, id);
    }

    @BeforeEach
    void setUp() {
        service = new BranchHealthService(branchRepository, analyzedCommitService);
    }

    private BranchProcessRequest makeRequest(String branchName, String commitHash) {
        BranchProcessRequest req = new BranchProcessRequest();
        req.targetBranchName = branchName;
        req.commitHash = commitHash;
        req.projectId = 1L;
        return req;
    }

    // ── markBranchHealthy ────────────────────────────────────────────────

    @Test
    void markBranchHealthy_branchExists_shouldMarkHealthyAndSave() throws Exception {
        Project project = new Project();
        setId(project, 1L);
        Branch branch = new Branch();

        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));

        service.markBranchHealthy(project, makeRequest("main", "abc123"));

        verify(branchRepository).save(branch);
    }

    @Test
    void markBranchHealthy_branchNotFound_shouldDoNothing() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.empty());

        service.markBranchHealthy(project, makeRequest("main", "abc123"));

        verify(branchRepository, never()).save(any());
    }

    // ── recordCommitsAnalyzed ────────────────────────────────────────────

    @Test
    void recordCommitsAnalyzed_nonEmptyList_shouldDelegateToService() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        service.recordCommitsAnalyzed(project, List.of("a", "b"), "main");

        verify(analyzedCommitService).recordBranchCommitsAnalyzed(project, List.of("a", "b"));
    }

    @Test
    void recordCommitsAnalyzed_emptyList_shouldSkip() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        service.recordCommitsAnalyzed(project, List.of(), "main");

        verifyNoInteractions(analyzedCommitService);
    }

    @Test
    void recordCommitsAnalyzed_serviceThrows_shouldSwallowException() throws Exception {
        Project project = new Project();
        setId(project, 1L);
        doThrow(new RuntimeException("DB down")).when(analyzedCommitService)
                .recordBranchCommitsAnalyzed(any(), anyList());

        // Should not throw
        service.recordCommitsAnalyzed(project, List.of("a"), "main");
    }

    // ── handleProcessFailure ─────────────────────────────────────────────

    @Test
    void handleProcessFailure_branchExists_shouldMarkStaleAndSave() throws Exception {
        Project project = new Project();
        setId(project, 1L);
        Branch branch = new Branch();

        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.of(branch));

        service.handleProcessFailure(project, makeRequest("main", "abc"), List.of("a"),
                new RuntimeException("fail"));

        verify(branchRepository).save(branch);
    }

    @Test
    void handleProcessFailure_branchNotFound_shouldNotThrow() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                .thenReturn(Optional.empty());

        // Should not throw
        service.handleProcessFailure(project, makeRequest("main", "abc"), List.of("a"),
                new RuntimeException("fail"));
    }

    @Test
    void handleProcessFailure_staleMarkingThrows_shouldNotPropagate() throws Exception {
        Project project = new Project();
        setId(project, 1L);

        when(branchRepository.findByProjectIdAndBranchName(anyLong(), anyString()))
                .thenThrow(new RuntimeException("DB error"));

        // Should not throw
        service.handleProcessFailure(project, makeRequest("main", "abc"), List.of("a"),
                new RuntimeException("original error"));
    }
}
