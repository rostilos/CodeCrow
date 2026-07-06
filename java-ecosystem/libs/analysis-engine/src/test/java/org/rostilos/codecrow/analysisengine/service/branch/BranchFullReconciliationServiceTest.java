package org.rostilos.codecrow.analysisengine.service.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.ProjectValidationService;
import org.rostilos.codecrow.analysisengine.service.PullRequestStatusSyncService;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;

import java.io.IOException;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anySet;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = org.mockito.quality.Strictness.LENIENT)
@DisplayName("BranchFullReconciliationService")
class BranchFullReconciliationServiceTest {

    @Mock private ProjectValidationService projectService;
    @Mock private BranchRepository branchRepository;
    @Mock private BranchIssueRepository branchIssueRepository;
    @Mock private PullRequestRepository pullRequestRepository;
    @Mock private AnalysisLockService analysisLockService;
    @Mock private BranchFileOperationsService branchFileOperationsService;
    @Mock private BranchIssueMappingService branchIssueMappingService;
    @Mock private BranchIssueReconciliationService branchIssueReconciliationService;
    @Mock private PullRequestStatusSyncService pullRequestStatusSyncService;
    @Mock private Project project;
    @Mock private VcsConnection vcsConnection;

    private BranchFullReconciliationService service;

    @BeforeEach
    void setUp() {
        service = new BranchFullReconciliationService(
                projectService,
                branchRepository,
                branchIssueRepository,
                pullRequestRepository,
                analysisLockService,
                branchFileOperationsService,
                branchIssueMappingService,
                branchIssueReconciliationService,
                pullRequestStatusSyncService);
    }

    @Nested
    @DisplayName("fullReconcile()")
    class FullReconcileTests {

        @Test
        @DisplayName("should throw when branch not found")
        void shouldThrowWhenBranchNotFound() throws IOException {
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.empty());

            assertThatThrownBy(() -> service.fullReconcile(1L, "main", consumer))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessageContaining("Branch not found");
        }

        @Test
        @DisplayName("should complete when no unresolved issues")
        void shouldCompleteWhenNoUnresolvedIssues() throws IOException {
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(pullRequestStatusSyncService.syncOpenPullRequestStates(project, consumer))
                    .thenReturn(PullRequestStatusSyncService.SyncResult.empty());

            Branch existingBranch = mock(Branch.class);
            when(existingBranch.getId()).thenReturn(10L);
            when(existingBranch.getLastSuccessfulCommitHash()).thenReturn("abc123");
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(existingBranch));
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            Branch branchWithIssues = mock(Branch.class);
            when(branchWithIssues.getIssues()).thenReturn(Collections.emptyList());
            when(branchRepository.findByIdWithIssues(10L)).thenReturn(Optional.of(branchWithIssues));

            Map<String, Object> result = service.fullReconcile(1L, "main", consumer);

            assertThat(result).containsEntry("status", "completed");
            assertThat(result).containsEntry("totalIssues", 0);
            assertThat(result).containsEntry("resolvedIssues", 0);
            assertThat(result).containsEntry("openPrsChecked", 0);
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should sync stale open PR statuses before importing merged PR issues")
        void shouldSyncStaleOpenPrStatusesBeforeImportingMergedPrIssues() throws IOException {
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(pullRequestStatusSyncService.syncOpenPullRequestStates(project, consumer))
                    .thenReturn(new PullRequestStatusSyncService.SyncResult(3, 1, 1, 1, 0));

            Branch existingBranch = branch("main", "abc123", 10L);
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(existingBranch));
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            mockVcsInfo();

            PullRequest mergedPr = new PullRequest();
            mergedPr.setPrNumber(42L);
            mergedPr.setTargetBranchName("main");
            mergedPr.setState(PullRequestState.MERGED);
            when(pullRequestRepository.findByProjectIdAndTargetBranchNameAndState(
                    1L, "main", PullRequestState.MERGED))
                    .thenReturn(List.of(mergedPr));
            when(branchIssueMappingService.findPrIssuePaths(1L, 42L))
                    .thenReturn(Set.of("src/App.java"));

            Map<String, String> syncArchive = Map.of("src/App.java", "dangerousCall();");
            when(branchFileOperationsService.downloadBranchArchive(any(), eq("abc123"), eq(Set.of("src/App.java"))))
                    .thenReturn(syncArchive);
            when(branchFileOperationsService.updateBranchFiles(eq(Set.of("src/App.java")), eq(project), eq("main"), eq(syncArchive)))
                    .thenReturn(Set.of("src/App.java"));
            when(branchIssueRepository.countAllByBranchId(10L)).thenReturn(0L, 1L);

            Branch branchBefore = mock(Branch.class);
            when(branchBefore.getIssues()).thenReturn(List.of(branchIssue("src/App.java", false)));

            Branch branchAfter = mock(Branch.class);
            when(branchAfter.getResolvedCount()).thenReturn(0);
            when(branchAfter.getTotalIssues()).thenReturn(1);

            when(branchRepository.findByIdWithIssues(10L))
                    .thenReturn(Optional.of(branchBefore))
                    .thenReturn(Optional.of(branchAfter));
            when(branchRepository.save(any(Branch.class))).thenAnswer(inv -> inv.getArgument(0));

            Map<String, Object> result = service.fullReconcile(1L, "main", consumer);

            verify(pullRequestStatusSyncService).syncOpenPullRequestStates(project, consumer);
            verify(branchIssueMappingService).mapCodeAnalysisIssuesToBranch(
                    eq(Set.of("src/App.java")), eq(Set.of("src/App.java")),
                    eq(existingBranch), eq(project), eq(42L));
            assertThat(result).containsEntry("openPrsChecked", 3);
            assertThat(result).containsEntry("openPrsStillOpen", 1);
            assertThat(result).containsEntry("openPrsMarkedMerged", 1);
            assertThat(result).containsEntry("openPrsMarkedDeclined", 1);
            assertThat(result).containsEntry("mergedPrsScanned", 1);
            assertThat(result).containsEntry("importedPrIssues", 1L);
            assertThat(result).containsEntry("openIssuesAfter", 1L);
            verify(analysisLockService).releaseLock("lock-key");
        }

        @Test
        @DisplayName("should report newly resolved count, not cumulative resolved count")
        void shouldReportNewlyResolvedCountNotCumulativeResolvedCount() throws IOException {
            Consumer<Map<String, Object>> consumer = mock(Consumer.class);

            when(projectService.getProjectWithConnections(1L)).thenReturn(project);
            when(project.getId()).thenReturn(1L);
            when(pullRequestStatusSyncService.syncOpenPullRequestStates(project, consumer))
                    .thenReturn(PullRequestStatusSyncService.SyncResult.empty());

            Branch existingBranch = mock(Branch.class);
            when(existingBranch.getId()).thenReturn(10L);
            when(existingBranch.getLastSuccessfulCommitHash()).thenReturn("abc123");
            when(branchRepository.findByProjectIdAndBranchName(1L, "main"))
                    .thenReturn(Optional.of(existingBranch));
            when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), any(), any()))
                    .thenReturn(Optional.of("lock-key"));

            mockVcsInfo();

            Branch branchBefore = mock(Branch.class);
            when(branchBefore.getIssues()).thenReturn(List.of(
                    branchIssue("old-1.java", true),
                    branchIssue("old-2.java", true),
                    branchIssue("old-3.java", true),
                    branchIssue("old-4.java", true),
                    branchIssue("old-5.java", true),
                    branchIssue("src/App.java", false),
                    branchIssue("src/Open.java", false)
            ));

            Branch branchAfter = mock(Branch.class);
            when(branchAfter.getResolvedCount()).thenReturn(6);
            when(branchAfter.getTotalIssues()).thenReturn(1);

            when(branchRepository.findByIdWithIssues(10L))
                    .thenReturn(Optional.of(branchBefore))
                    .thenReturn(Optional.of(branchAfter));
            when(branchRepository.save(any(Branch.class))).thenAnswer(inv -> inv.getArgument(0));

            Map<String, String> archiveContents = Map.of(
                    "src/App.java", "safeCall();",
                    "src/Open.java", "dangerousCall();");
            when(branchFileOperationsService.downloadBranchArchive(any(), eq("abc123"), anySet()))
                    .thenReturn(archiveContents);
            when(branchFileOperationsService.updateBranchFiles(anySet(), eq(project), eq("main"), eq(archiveContents)))
                    .thenReturn(Set.of("src/App.java", "src/Open.java"));

            Map<String, Object> result = service.fullReconcile(1L, "main", consumer);

            assertThat(result).containsEntry("status", "completed");
            assertThat(result).containsEntry("openIssuesBefore", 2L);
            assertThat(result).containsEntry("openIssuesAfter", 1L);
            assertThat(result).containsEntry("resolvedIssuesBefore", 5L);
            assertThat(result).containsEntry("resolvedIssuesAfter", 6L);
            assertThat(result).containsEntry("resolvedIssues", 1L);
            assertThat(result).containsEntry("totalIssues", 1L);
            assertThat((String) result.get("message")).contains("1 newly resolved");
            verify(branchIssueReconciliationService).reanalyzeCandidateIssues(
                    anySet(), anySet(), eq(existingBranch), eq(project), any(), eq(consumer), eq(archiveContents));
            verify(analysisLockService).releaseLock("lock-key");
        }
    }

    private void mockVcsInfo() {
        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
        when(repoInfo.getRepoWorkspace()).thenReturn("ws");
        when(repoInfo.getRepoSlug()).thenReturn("repo");
    }

    private Branch branch(String branchName, String commitHash, Long id) {
        Branch branch = new Branch();
        branch.setBranchName(branchName);
        branch.setLastSuccessfulCommitHash(commitHash);
        setBranchId(branch, id);
        return branch;
    }

    private void setBranchId(Branch branch, Long id) {
        try {
            var field = Branch.class.getDeclaredField("id");
            field.setAccessible(true);
            field.set(branch, id);
        } catch (Exception e) {
            throw new IllegalStateException("Could not set branch id for test", e);
        }
    }

    private BranchIssue branchIssue(String filePath, boolean resolved) {
        BranchIssue issue = new BranchIssue();
        issue.setFilePath(filePath);
        issue.setResolved(resolved);
        return issue;
    }
}
