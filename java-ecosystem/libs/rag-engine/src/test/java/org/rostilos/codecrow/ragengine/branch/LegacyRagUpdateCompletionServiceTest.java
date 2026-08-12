package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.rag.RagBranchIndex;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class LegacyRagUpdateCompletionServiceTest {
    private JobRepository jobs;
    private RagIndexTrackingService tracking;
    private RagIndexStatusRepository statuses;
    private RagBranchIndexRepository branches;
    private LegacyRagUpdateCompletionService completion;
    private Project project;

    @BeforeEach
    void setUp() {
        jobs = mock(JobRepository.class);
        tracking = mock(RagIndexTrackingService.class);
        statuses = mock(RagIndexStatusRepository.class);
        branches = mock(RagBranchIndexRepository.class);
        completion = new LegacyRagUpdateCompletionService(
                jobs, tracking, statuses, branches);
        project = new Project();
        ReflectionTestUtils.setField(project, "id", 42L);
    }

    @Test
    void recoveryWinningTheJobCasPreventsEveryCheckpointWrite() {
        OffsetDateTime validAfter = OffsetDateTime.now().minusMinutes(2);
        when(jobs.completeOwnedLegacyRagJob(eq(91L), eq(validAfter), any()))
                .thenReturn(0);

        boolean completed = completion.complete(
                project, "main", "commit-b", 91L, validAfter,
                true, 2, 1, 30, Set.of("old.java"));

        assertThat(completed).isFalse();
        verifyNoInteractions(tracking, branches);
    }

    @Test
    void ownedJobAndAllCheckpointsAdvanceTogether() {
        OffsetDateTime validAfter = OffsetDateTime.now().minusMinutes(2);
        RagBranchIndex branch = new RagBranchIndex(project, "main");
        branch.setCommitHash("commit-a");
        branch.setDeletedFiles(new java.util.HashSet<>(Set.of("older.java")));
        when(jobs.completeOwnedLegacyRagJob(eq(91L), eq(validAfter), any()))
                .thenReturn(1);
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.UPDATING);
        status.setActiveJobId(91L);
        when(statuses.findByProjectIdForUpdate(42L))
                .thenReturn(Optional.of(status));
        when(branches.findByProjectIdAndBranchNameForUpdate(42L, "main"))
                .thenReturn(Optional.of(branch));

        boolean completed = completion.complete(
                project, "main", "commit-b", 91L, validAfter,
                true, 2, 1, 30, Set.of("old.java"));

        assertThat(completed).isTrue();
        verify(tracking).markUpdatingCompleted(
                project, "main", "commit-b", 2, 1, 30, 91L);
        verify(branches).save(argThat(saved ->
                "commit-b".equals(saved.getCommitHash())
                        && saved.getDeletedFiles().equals(
                            Set.of("older.java", "old.java"))));
    }

    @Test
    void newerProjectStatusOwnerFencesEveryCheckpointWrite() {
        OffsetDateTime validAfter = OffsetDateTime.now().minusMinutes(2);
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.UPDATING);
        status.setActiveJobId(92L);
        when(jobs.completeOwnedLegacyRagJob(eq(91L), eq(validAfter), any()))
                .thenReturn(1);
        when(statuses.findByProjectIdForUpdate(42L))
                .thenReturn(Optional.of(status));

        org.assertj.core.api.Assertions.assertThatThrownBy(() -> completion.complete(
                project, "main", "commit-b", 91L, validAfter,
                true, 2, 1, 30, Set.of("old.java")))
                .isInstanceOf(LegacyRagUpdateCompletionService
                        .LegacyRagCompletionConflictException.class);

        verifyNoInteractions(tracking, branches);
    }

    @Test
    void nonPrimaryBranchDoesNotTouchProjectStatus() {
        OffsetDateTime validAfter = OffsetDateTime.now().minusMinutes(2);
        when(jobs.completeOwnedLegacyRagJob(eq(91L), eq(validAfter), any()))
                .thenReturn(1);
        when(branches.findByProjectIdAndBranchNameForUpdate(42L, "feature"))
                .thenReturn(Optional.empty());

        assertThat(completion.complete(
                project, "feature", "commit-b", 91L, validAfter,
                false, 2, 1, null, Set.of())).isTrue();

        verifyNoInteractions(tracking);
        verify(branches).save(argThat(saved ->
                "feature".equals(saved.getBranchName())
                        && "commit-b".equals(saved.getCommitHash())));
    }
}
