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
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.util.tracking.AnchoredIssueIdentity;
import org.rostilos.codecrow.core.util.tracking.IssueFingerprint;

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
            // Simulate: the origin ID 42 is linked
            BranchIssue linkedBi = mock(BranchIssue.class);
            CodeAnalysisIssue originIssue = new CodeAnalysisIssue();
            setId(originIssue, 42L);
            when(linkedBi.getOriginIssue()).thenReturn(originIssue);
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of(linkedBi));

            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "a.java"))
                    .thenReturn(List.of(issue));
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
            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "a.java"))
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
            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(1L, "main", "a.java"))
                    .thenReturn(List.of(resolved));
            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("a.java"), Set.of("a.java"), branch, project);

            verify(branchIssueRepository, never()).saveAndFlush(any());
        }

        @Test
        void sourcePrNumber_shouldMapOnlyUnresolvedIssuesFromThatPrNewestFirst() throws Exception {
            Branch branch = new Branch();
            setId(branch, 1L);
            branch.setBranchName("main");
            Project project = new Project();
            setId(project, 1L);

            CodeAnalysisIssue unresolvedFromPr = new CodeAnalysisIssue();
            setId(unresolvedFromPr, 101L);
            unresolvedFromPr.setResolved(false);
            unresolvedFromPr.setFilePath("src/App.java");
            unresolvedFromPr.setLineNumber(12);
            unresolvedFromPr.setSeverity(IssueSeverity.HIGH);
            unresolvedFromPr.setIssueCategory(IssueCategory.BUG_RISK);
            unresolvedFromPr.setTitle("Anchored bug");
            unresolvedFromPr.setContentFingerprint("newest-content-fp");

            CodeAnalysisIssue resolvedFromPr = new CodeAnalysisIssue();
            setId(resolvedFromPr, 102L);
            resolvedFromPr.setResolved(true);
            resolvedFromPr.setFilePath("src/App.java");

            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            when(codeAnalysisIssueRepository.findByProjectIdAndPrNumberAndFilePathNewestFirst(
                    1L, 42L, "src/App.java"))
                    .thenReturn(List.of(unresolvedFromPr, resolvedFromPr));
            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("src/App.java"), Set.of("src/App.java"), branch, project, 42L);

            verify(codeAnalysisIssueRepository).findByProjectIdAndPrNumberAndFilePathNewestFirst(
                    1L, 42L, "src/App.java");
            verify(codeAnalysisIssueRepository, never()).findByProjectIdAndBranchNameAndFilePath(
                    anyLong(), anyString(), anyString());
            ArgumentCaptor<BranchIssue> branchIssueCaptor = ArgumentCaptor.forClass(BranchIssue.class);
            verify(branchIssueRepository).saveAndFlush(branchIssueCaptor.capture());
            assertThat(branchIssueCaptor.getValue().getOriginIssue()).isEqualTo(unresolvedFromPr);
        }

        @Test
        void sourcePrNumber_shouldShadowOlderLineageIssuesWhenNewestIterationResolvedOrCarriedForward() throws Exception {
            Branch branch = new Branch();
            setId(branch, 1L);
            branch.setBranchName("main");
            Project project = new Project();
            setId(project, 1L);

            CodeAnalysisIssue pr1Risky = prIssue(101L, "src/App.java", "Risky call remains", false, null);
            CodeAnalysisIssue pr2RiskyResolved = prIssue(102L, "src/App.java", "Risky call remains", true, 101L);
            CodeAnalysisIssue pr2Leak = prIssue(201L, "src/App.java", "Secret leak remains", false, null);
            CodeAnalysisIssue pr3Leak = prIssue(202L, "src/App.java", "Secret leak remains", false, 201L);

            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            when(codeAnalysisIssueRepository.findByProjectIdAndPrNumberAndFilePathNewestFirst(
                    1L, 42L, "src/App.java"))
                    .thenReturn(List.of(pr3Leak, pr2RiskyResolved, pr2Leak, pr1Risky));
            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("src/App.java"), Set.of("src/App.java"), branch, project, 42L);

            ArgumentCaptor<BranchIssue> branchIssueCaptor = ArgumentCaptor.forClass(BranchIssue.class);
            verify(branchIssueRepository, times(1)).saveAndFlush(branchIssueCaptor.capture());
            assertThat(branchIssueCaptor.getValue().getOriginIssue()).isEqualTo(pr3Leak);
        }

        @Test
        void mergedPrBatch_shouldMapIssuesFromEveryCompletedPr() throws Exception {
            Branch branch = new Branch();
            setId(branch, 1L);
            branch.setBranchName("main");
            Project project = new Project();
            setId(project, 1L);

            CodeAnalysisIssue prOneIssue = prIssue(101L, "src/One.java", "PR one issue", false, null);
            CodeAnalysisIssue prTwoIssue = prIssue(201L, "src/Two.java", "PR two issue", false, null);
            CodeAnalysisIssue prThreeIssue = prIssue(301L, "src/Three.java", "PR three issue", false, null);

            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            when(codeAnalysisIssueRepository.findByProjectIdAndPrNumberInAndFilePathNewestFirst(
                    1L, Set.of(11L, 12L, 13L), "src/One.java"))
                    .thenReturn(List.of(prOneIssue));
            when(codeAnalysisIssueRepository.findByProjectIdAndPrNumberInAndFilePathNewestFirst(
                    1L, Set.of(11L, 12L, 13L), "src/Two.java"))
                    .thenReturn(List.of(prTwoIssue));
            when(codeAnalysisIssueRepository.findByProjectIdAndPrNumberInAndFilePathNewestFirst(
                    1L, Set.of(11L, 12L, 13L), "src/Three.java"))
                    .thenReturn(List.of(prThreeIssue));
            Set<String> files = Set.of("src/One.java", "src/Two.java", "src/Three.java");
            service.mapCodeAnalysisIssuesToBranch(
                    files, files, branch, project, Set.of(11L, 12L, 13L));

            ArgumentCaptor<BranchIssue> captor = ArgumentCaptor.forClass(BranchIssue.class);
            verify(branchIssueRepository, times(3)).saveAndFlush(captor.capture());
            assertThat(captor.getAllValues())
                    .extracting(BranchIssue::getOriginIssue)
                    .containsExactlyInAnyOrder(prOneIssue, prTwoIssue, prThreeIssue);
        }

        @Test
        void identicalAnchorsInDifferentFiles_shouldBothMap() throws Exception {
            Branch branch = branch(1L);
            Project project = project(1L);
            String lineHash = "0123456789abcdef0123456789abcdef";
            CodeAnalysisIssue first = anchoredIssue(
                    101L, "src/First.java", "Unchecked result", lineHash);
            CodeAnalysisIssue second = anchoredIssue(
                    102L, "src/Second.java", "Unchecked result", lineHash);

            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(
                    1L, "main", "src/First.java")).thenReturn(List.of(first));
            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(
                    1L, "main", "src/Second.java")).thenReturn(List.of(second));

            Set<String> files = Set.of("src/First.java", "src/Second.java");
            service.mapCodeAnalysisIssuesToBranch(files, files, branch, project);

            ArgumentCaptor<BranchIssue> captor = ArgumentCaptor.forClass(BranchIssue.class);
            verify(branchIssueRepository, times(2)).saveAndFlush(captor.capture());
            assertThat(captor.getAllValues())
                    .extracting(BranchIssue::getFilePath)
                    .containsExactly("src/First.java", "src/Second.java");
            assertThat(captor.getAllValues())
                    .extracting(BranchIssue::getContentFingerprint)
                    .doesNotHaveDuplicates();
        }

        @Test
        void sameAnchorAndCategoryWithDifferentTitles_shouldBothMap() throws Exception {
            Branch branch = branch(1L);
            Project project = project(1L);
            String lineHash = "0123456789abcdef0123456789abcdef";
            CodeAnalysisIssue first = anchoredIssue(
                    101L, "src/App.java", "Authorization bypass", lineHash);
            CodeAnalysisIssue second = anchoredIssue(
                    102L, "src/App.java", "Transaction is not committed", lineHash);

            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(
                    1L, "main", "src/App.java")).thenReturn(List.of(first, second));

            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("src/App.java"),
                    Set.of("src/App.java"),
                    branch,
                    project
            );

            verify(branchIssueRepository, times(2)).saveAndFlush(any(BranchIssue.class));
        }

        @Test
        void unanchoredFindingsWithSameLegacyLocation_shouldBothMapAndBypassUniqueIndex()
                throws Exception {
            Branch branch = branch(1L);
            Project project = project(1L);
            CodeAnalysisIssue first = unanchoredIssue(101L, "src/App.java", "File contract");
            CodeAnalysisIssue second = unanchoredIssue(102L, "src/App.java", "File contract");

            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(
                    1L, "main", "src/App.java")).thenReturn(List.of(first, second));

            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("src/App.java"),
                    Set.of("src/App.java"),
                    branch,
                    project
            );

            ArgumentCaptor<BranchIssue> captor = ArgumentCaptor.forClass(BranchIssue.class);
            verify(branchIssueRepository, times(2)).saveAndFlush(captor.capture());
            assertThat(captor.getAllValues())
                    .extracting(BranchIssue::getContentFingerprint)
                    .containsOnlyNulls();
        }

        @Test
        void resolvedHistoricalIdentity_shouldNotSuppressRecurrence() throws Exception {
            Branch branch = branch(1L);
            Project project = project(1L);
            CodeAnalysisIssue historicalOrigin = anchoredIssue(
                    100L,
                    "src/App.java",
                    "Unchecked result",
                    "0123456789abcdef0123456789abcdef"
            );
            BranchIssue resolved = BranchIssue.fromCodeAnalysisIssue(historicalOrigin, branch);
            resolved.setResolved(true);
            // Model an existing row written before path-aware storage identities.
            resolved.setContentFingerprint(historicalOrigin.getContentFingerprint());

            CodeAnalysisIssue recurrence = anchoredIssue(
                    101L,
                    "src/App.java",
                    "Unchecked result",
                    "0123456789abcdef0123456789abcdef"
            );
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of(resolved));
            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(
                    1L, "main", "src/App.java")).thenReturn(List.of(recurrence));

            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("src/App.java"),
                    Set.of("src/App.java"),
                    branch,
                    project
            );

            verify(branchIssueRepository).saveAllAndFlush(any());
            assertThat(resolved.getContentFingerprint()).isNull();
            verify(branchIssueRepository).saveAndFlush(argThat(issue ->
                    issue.getOriginIssue() == recurrence
                            && !issue.isResolved()
                            && issue.getContentFingerprint() != null));
        }

        @Test
        void unresolvedHistoricalRow_shouldDeduplicateByRecomputedAnchoredIdentity()
                throws Exception {
            Branch branch = branch(1L);
            Project project = project(1L);
            String lineHash = "0123456789abcdef0123456789abcdef";
            CodeAnalysisIssue historicalOrigin = anchoredIssue(
                    100L, "src/App.java", "Unchecked result", lineHash);
            BranchIssue existing = BranchIssue.fromCodeAnalysisIssue(historicalOrigin, branch);
            existing.setContentFingerprint(historicalOrigin.getContentFingerprint());

            CodeAnalysisIssue repeated = anchoredIssue(
                    101L, "src/App.java", "Unchecked result", lineHash);
            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of(existing));
            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(
                    1L, "main", "src/App.java")).thenReturn(List.of(repeated));

            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("src/App.java"),
                    Set.of("src/App.java"),
                    branch,
                    project
            );

            verify(branchIssueRepository, never()).saveAndFlush(any());
        }

        @Test
        void nullOriginIds_shouldNeverBecomeASharedIdentity() throws Exception {
            Branch branch = branch(1L);
            Project project = project(1L);
            CodeAnalysisIssue first = unanchoredIssue(null, "src/App.java", "First");
            CodeAnalysisIssue second = unanchoredIssue(null, "src/App.java", "Second");

            when(branchIssueRepository.findByBranchId(1L)).thenReturn(List.of());
            when(codeAnalysisIssueRepository.findByProjectIdAndBranchNameAndFilePath(
                    1L, "main", "src/App.java")).thenReturn(List.of(first, second));

            service.mapCodeAnalysisIssuesToBranch(
                    Set.of("src/App.java"),
                    Set.of("src/App.java"),
                    branch,
                    project
            );

            verify(branchIssueRepository, times(2)).saveAndFlush(any(BranchIssue.class));
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

    @Test
    void findPrIssuePaths_shouldCombineAllPrsInTheMergeBatch() throws Exception {
        CodeAnalysisIssue first = prIssue(1L, "src/One.java", "one", false, null);
        CodeAnalysisIssue second = prIssue(2L, "src/Two.java", "two", false, null);
        CodeAnalysisIssue resolved = prIssue(3L, "src/Resolved.java", "resolved", true, null);
        when(codeAnalysisIssueRepository.findByProjectIdAndPrNumberIn(
                1L, Set.of(11L, 12L, 13L)))
                .thenReturn(List.of(first, second, resolved));

        Set<String> result = service.findPrIssuePaths(1L, Set.of(11L, 12L, 13L));

        assertThat(result).containsExactlyInAnyOrder("src/One.java", "src/Two.java");
    }

    private static CodeAnalysisIssue prIssue(Long id, String filePath, String title, boolean resolved,
                                             Long trackedFromIssueId) throws Exception {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        setId(issue, id);
        issue.setFilePath(filePath);
        issue.setLineNumber(6);
        issue.setSeverity(IssueSeverity.HIGH);
        issue.setIssueCategory(IssueCategory.BUG_RISK);
        issue.setTitle(title);
        issue.setResolved(resolved);
        issue.setTrackedFromIssueId(trackedFromIssueId);
        return issue;
    }

    private static Branch branch(Long id) throws Exception {
        Branch branch = new Branch();
        setId(branch, id);
        branch.setBranchName("main");
        return branch;
    }

    private static Project project(Long id) throws Exception {
        Project project = new Project();
        setId(project, id);
        return project;
    }

    private static CodeAnalysisIssue anchoredIssue(
            Long id,
            String filePath,
            String title,
            String lineHash
    ) throws Exception {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        if (id != null) {
            setId(issue, id);
        }
        issue.setFilePath(filePath);
        issue.setLineNumber(12);
        issue.setSeverity(IssueSeverity.HIGH);
        issue.setIssueCategory(IssueCategory.BUG_RISK);
        issue.setIssueScope(IssueScope.LINE);
        issue.setTitle(title);
        issue.setReason("Exact defect evidence");
        issue.setLineHash(lineHash);
        issue.setCodeSnippet("dangerousCall();");
        issue.setIssueFingerprint(IssueFingerprint.compute(
                issue.getIssueCategory(), lineHash, title));
        issue.setContentFingerprint(
                IssueFingerprint.computeContentFingerprint(lineHash, title));
        return issue;
    }

    private static CodeAnalysisIssue unanchoredIssue(
            Long id,
            String filePath,
            String title
    ) throws Exception {
        CodeAnalysisIssue issue = anchoredIssue(id, filePath, title, null);
        issue.setLineNumber(1);
        issue.setIssueScope(IssueScope.FILE);
        issue.setCodeSnippet(null);
        assertThat(AnchoredIssueIdentity.forBranchStorage(issue)).isNull();
        return issue;
    }
}
