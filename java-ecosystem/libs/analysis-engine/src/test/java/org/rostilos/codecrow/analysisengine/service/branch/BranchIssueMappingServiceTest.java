package org.rostilos.codecrow.analysisengine.service.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class BranchIssueMappingServiceTest {

    @Mock private CodeAnalysisIssueRepository codeAnalysisIssueRepository;
    @Mock private BranchIssueRepository branchIssueRepository;

    private BranchIssueMappingService service;

    private static void setId(Object entity, Long id) throws Exception {
        Field f = entity.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(entity, id);
    }

    @BeforeEach
    void setUp() {
        service = new BranchIssueMappingService(codeAnalysisIssueRepository, branchIssueRepository);
    }

    // ── mapCodeAnalysisIssuesToBranch ─────────────────────────────────────

    @Nested
    class MapIssuesToBranch {

        @Test
        void emptyChangedFiles_shouldNotQuery() throws Exception {
            Branch branch = new Branch();
            setId(branch, 1L);
            Project project = new Project();
            setId(project, 1L);
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());

            service.mapCodeAnalysisIssuesToBranch(Set.of(), Set.of(), branch, project);

            verifyNoInteractions(codeAnalysisIssueRepository);
        }

        @Test
        void fileNotInBranch_shouldSkip() throws Exception {
            Branch branch = new Branch();
            setId(branch, 1L);
            branch.setBranchName("main");
            Project project = new Project();
            setId(project, 1L);
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());

            // changedFiles has "a.java" but filesExistingInBranch doesn't
            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("a.java"), Set.of("b.java"), branch, project);

            verifyNoInteractions(codeAnalysisIssueRepository);
        }

        @Test
        void unresolvedIssueAlreadyLinked_shouldUpdateSeverityOnly() throws Exception {
            Branch branch = new Branch();
            setId(branch, 1L);
            branch.setBranchName("main");
            Project project = new Project();
            setId(project, 1L);

            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            setId(issue, 42L);
            issue.setResolved(false);
            issue.setSeverity(IssueSeverity.HIGH);

            BranchIssue existingBi = new BranchIssue();
            setId(existingBi, 100L);
            existingBi.setSeverity(IssueSeverity.MEDIUM);

            // Pre-load: branch issue is already linked to origin issue 42
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            // Simulate: the origin ID 42 is linked
            BranchIssue linkedBi = mock(BranchIssue.class);
            CodeAnalysisIssue originIssue = new CodeAnalysisIssue();
            setId(originIssue, 42L);
            when(linkedBi.getOriginIssue()).thenReturn(originIssue);
            when(linkedBi.getContentFingerprint()).thenReturn(null);
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of(linkedBi));

            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "a.java"))
                    .thenReturn(List.of(issue));
            when(branchIssueRepository.findByBranchIdAndFilePath(1L, "a.java"))
                    .thenReturn(List.of());
            when(branchIssueRepository.findByBranchIdAndOriginIssueId(1L, 42L))
                    .thenReturn(Optional.of(existingBi));

            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("a.java"), Set.of("a.java"), branch, project);

            // Should update severity on existing branch issue
            verify(branchIssueRepository).findByBranchIdAndOriginIssueId(1L, 42L);
        }

        @Test
        void noIssuesForFile_shouldNotCreateBranchIssues() throws Exception {
            Branch branch = new Branch();
            setId(branch, 1L);
            branch.setBranchName("main");
            Project project = new Project();
            setId(project, 1L);
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "a.java"))
                    .thenReturn(List.of());

            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("a.java"), Set.of("a.java"), branch, project);

            verify(branchIssueRepository, never()).saveAndFlush(any());
        }

        @Test
        void resolvedIssues_shouldBeFilteredOut() throws Exception {
            Branch branch = new Branch();
            setId(branch, 1L);
            branch.setBranchName("main");
            Project project = new Project();
            setId(project, 1L);

            CodeAnalysisIssue resolved = new CodeAnalysisIssue();
            setId(resolved, 50L);
            resolved.setResolved(true);

            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            when(codeAnalysisIssueRepository.findByProjectIdAndFilePath(1L, "a.java"))
                    .thenReturn(List.of(resolved));
            when(branchIssueRepository.findByBranchIdAndFilePath(1L, "a.java"))
                    .thenReturn(List.of());

            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("a.java"), Set.of("a.java"), branch, project);

            verify(branchIssueRepository, never()).saveAndFlush(any());
        }
    }

    // ── findPrIssuePaths ────────────────────────────────────────────────

    @Test
    void findPrIssuePaths_shouldReturnUnresolvedFilePaths() throws Exception {
        CodeAnalysisIssue issue1 = new CodeAnalysisIssue();
        setId(issue1, 1L);
        issue1.setResolved(false);
        issue1.setFilePath("src/Foo.java");

        CodeAnalysisIssue issue2 = new CodeAnalysisIssue();
        setId(issue2, 2L);
        issue2.setResolved(true);
        issue2.setFilePath("src/Bar.java");

        CodeAnalysisIssue issue3 = new CodeAnalysisIssue();
        setId(issue3, 3L);
        issue3.setResolved(false);
        issue3.setFilePath("src/Baz.java");

        when(codeAnalysisIssueRepository.findByProjectIdAndPrNumber(1L, 42L))
                .thenReturn(List.of(issue1, issue2, issue3));

        Set<String> result = service.findPrIssuePaths(1L, 42L);
        assertThat(result).containsExactlyInAnyOrder("src/Foo.java", "src/Baz.java");
    }

    @Test
    void findPrIssuePaths_nullFilePaths_shouldBeExcluded() throws Exception {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        setId(issue, 1L);
        issue.setResolved(false);
        issue.setFilePath(null);

        when(codeAnalysisIssueRepository.findByProjectIdAndPrNumber(1L, 1L))
                .thenReturn(List.of(issue));

        Set<String> result = service.findPrIssuePaths(1L, 1L);
        assertThat(result).isEmpty();
    }

    // ── Static legacy key builders ──────────────────────────────────────

    @Test
    void buildLegacyContentKey_shouldCombineFields() {
        BranchIssue bi = new BranchIssue();
        bi.setFilePath("src/Foo.java");
        bi.setLineNumber(42);
        bi.setSeverity(IssueSeverity.HIGH);
        bi.setIssueCategory(IssueCategory.BEST_PRACTICES);
        bi.setTitle("Some Title");

        String key = BranchIssueMappingService.buildLegacyContentKey(bi);
        assertThat(key).contains("src/Foo.java").contains("42").contains("HIGH").contains("BEST_PRACTICES");
    }

    @Test
    void buildLegacyContentKeyFromCAI_shouldCombineFields() {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        issue.setFilePath("src/Bar.java");
        issue.setLineNumber(10);
        issue.setSeverity(IssueSeverity.MEDIUM);
        issue.setIssueCategory(IssueCategory.BEST_PRACTICES);
        issue.setTitle("Another Title");

        String key = BranchIssueMappingService.buildLegacyContentKeyFromCAI(issue);
        assertThat(key).contains("src/Bar.java").contains("10").contains("MEDIUM");
    }
}
