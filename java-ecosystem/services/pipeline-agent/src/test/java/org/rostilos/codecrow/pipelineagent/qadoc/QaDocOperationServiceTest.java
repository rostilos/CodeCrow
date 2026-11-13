package org.rostilos.codecrow.pipelineagent.qadoc;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.project.Project;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class QaDocOperationServiceTest {
    @Test void releasesTheFirstLeaseWhenTheSecondScopeCannotBeAcquired() {
        var locks = mock(AnalysisLockService.class); var project = mock(Project.class);
        var lease = mock(AnalysisLockService.LockLease.class);
        when(locks.acquireLockWithWait(eq(project), anyString(), eq(AnalysisLockType.QA_DOCUMENTATION), isNull(), eq(7L), any()))
                .thenReturn(Optional.of("pr-lease"), Optional.empty());
        when(locks.maintainLockLease(eq("pr-lease"), anyInt())).thenReturn(lease);
        var service = new QaDocOperationService(locks);
        assertThatThrownBy(() -> service.open(project, 7L, "TASK-1", ignored -> {})).isInstanceOf(IllegalStateException.class);
        verify(lease).close(); verify(locks).releaseLock("pr-lease");
    }

    @Test void checksBothOwnersBeforePublicationAndReleasesBothOnFailure() {
        var locks = mock(AnalysisLockService.class); var project = mock(Project.class);
        var pr = mock(AnalysisLockService.LockLease.class); var task = mock(AnalysisLockService.LockLease.class);
        when(locks.acquireLockWithWait(eq(project), anyString(), eq(AnalysisLockType.QA_DOCUMENTATION), isNull(), eq(7L), any()))
                .thenReturn(Optional.of("pr-lease"), Optional.of("task-lease"));
        when(locks.maintainLockLease(eq("pr-lease"), anyInt())).thenReturn(pr);
        when(locks.maintainLockLease(eq("task-lease"), anyInt())).thenReturn(task);
        when(pr.confirmOwnership()).thenReturn(true); when(task.confirmOwnership()).thenReturn(true, false);
        var service = new QaDocOperationService(locks);
        try (var operation = service.open(project, 7L, "task-1", ignored -> {})) {
            assertThatThrownBy(operation::assertOwnership).isInstanceOf(IllegalStateException.class);
        }
        verify(locks).acquireLockWithWait(eq(project), eq("qa-pr:7"), any(), isNull(), eq(7L), any());
        verify(locks).acquireLockWithWait(eq(project), eq("qa-task:TASK-1"), any(), isNull(), eq(7L), any());
        verify(pr).close(); verify(task).close();
        verify(locks).releaseLock("pr-lease"); verify(locks).releaseLock("task-lease");
    }
}
