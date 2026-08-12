package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.service.JobService;

import java.time.OffsetDateTime;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

class LegacyRagJobLeaseServiceTest {

    @Test
    void renewsSynchronouslyThenHeartbeatsUntilClosed() {
        JobService jobs = mock(JobService.class);
        ScheduledExecutorService executor = mock(ScheduledExecutorService.class);
        ScheduledFuture<?> scheduled = mock(ScheduledFuture.class);
        when(jobs.renewLegacyRagJobLease(eq(91L), any(), any()))
                .thenReturn(true);
        doReturn(scheduled).when(executor).scheduleWithFixedDelay(
                any(Runnable.class), eq(15L), eq(15L), eq(TimeUnit.SECONDS));
        LegacyRagJobLeaseService service = new LegacyRagJobLeaseService(
                jobs, executor, 60, 15);

        LegacyRagJobLeaseService.JobLease lease = service.start(91L);

        var heartbeat = org.mockito.ArgumentCaptor.forClass(Runnable.class);
        verify(executor).scheduleWithFixedDelay(
                heartbeat.capture(), eq(15L), eq(15L), eq(TimeUnit.SECONDS));
        verify(jobs, times(1)).renewLegacyRagJobLease(eq(91L), any(), any());
        heartbeat.getValue().run();
        verify(jobs, times(2)).renewLegacyRagJobLease(eq(91L), any(), any());
        assertThat(lease.confirmOwnership()).isTrue();
        verify(jobs, times(3)).renewLegacyRagJobLease(eq(91L), any(), any());

        lease.close();
        verify(scheduled).cancel(false);
    }

    @Test
    void refusesWorkWhenTheInitialLeaseCannotBeProven() {
        JobService jobs = mock(JobService.class);
        ScheduledExecutorService executor = mock(ScheduledExecutorService.class);
        when(jobs.renewLegacyRagJobLease(eq(92L), any(), any()))
                .thenReturn(false);
        LegacyRagJobLeaseService service = new LegacyRagJobLeaseService(
                jobs, executor, 60, 15);

        LegacyRagJobLeaseService.JobLease lease = service.start(92L);

        assertThat(lease.isOwnershipLost()).isTrue();
        assertThat(lease.confirmOwnership()).isFalse();
        verifyNoInteractions(executor);
    }

    @Test
    void transientHeartbeatFailureDoesNotDiscardAStillValidLease() {
        JobService jobs = mock(JobService.class);
        ScheduledExecutorService executor = mock(ScheduledExecutorService.class);
        ScheduledFuture<?> scheduled = mock(ScheduledFuture.class);
        when(jobs.renewLegacyRagJobLease(eq(93L), any(), any()))
                .thenReturn(true)
                .thenThrow(new IllegalStateException("temporary database outage"))
                .thenReturn(true);
        doReturn(scheduled).when(executor).scheduleWithFixedDelay(
                any(Runnable.class), anyLong(), anyLong(), any());
        LegacyRagJobLeaseService service = new LegacyRagJobLeaseService(
                jobs, executor, 60, 15);

        LegacyRagJobLeaseService.JobLease lease = service.start(93L);
        var heartbeat = org.mockito.ArgumentCaptor.forClass(Runnable.class);
        verify(executor).scheduleWithFixedDelay(
                heartbeat.capture(), anyLong(), anyLong(), any());

        heartbeat.getValue().run();
        assertThat(lease.isOwnershipLost()).isFalse();
        assertThat(lease.confirmOwnership()).isTrue();
    }

    @Test
    void renewalUsesARealLeaseWindow() {
        JobService jobs = mock(JobService.class);
        ScheduledExecutorService executor = mock(ScheduledExecutorService.class);
        when(jobs.renewLegacyRagJobLease(eq(94L), any(), any()))
                .thenReturn(true);
        doReturn(mock(ScheduledFuture.class)).when(executor).scheduleWithFixedDelay(
                any(Runnable.class), anyLong(), anyLong(), any());
        LegacyRagJobLeaseService service = new LegacyRagJobLeaseService(
                jobs, executor, 60, 15);

        service.start(94L);

        var validAfter = org.mockito.ArgumentCaptor.forClass(OffsetDateTime.class);
        var renewedAt = org.mockito.ArgumentCaptor.forClass(OffsetDateTime.class);
        verify(jobs).renewLegacyRagJobLease(
                eq(94L), validAfter.capture(), renewedAt.capture());
        assertThat(validAfter.getValue()).isEqualTo(
                renewedAt.getValue().minusSeconds(60));
    }
}
