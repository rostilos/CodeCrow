package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;

import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

class RagIndexOperationHeartbeatServiceTest {

    @Test
    void scopeSchedulesHeartbeatsAndCancelsOnClose() {
        RagBranchIndexRegistryService registry = mock(RagBranchIndexRegistryService.class);
        ScheduledExecutorService executor = mock(ScheduledExecutorService.class);
        @SuppressWarnings("unchecked")
        ScheduledFuture<Object> scheduled = mock(ScheduledFuture.class);
        doReturn(scheduled).when(executor).scheduleAtFixedRate(
                any(Runnable.class), eq(15L), eq(15L), eq(TimeUnit.SECONDS));
        RagIndexOperationHeartbeatService service =
                new RagIndexOperationHeartbeatService(registry, executor, 15L);

        RagIndexOperationHeartbeatService.HeartbeatScope scope = service.start(91L);

        var heartbeat = org.mockito.ArgumentCaptor.forClass(Runnable.class);
        verify(executor).scheduleAtFixedRate(
                heartbeat.capture(), eq(15L), eq(15L), eq(TimeUnit.SECONDS));
        heartbeat.getValue().run();
        verify(registry).heartbeatBuild(91L);

        scope.close();
        verify(scheduled).cancel(false);
        service.close();
        verify(executor).shutdownNow();
    }

    @Test
    void transientHeartbeatFailureDoesNotEscapeSchedulerTask() {
        RagBranchIndexRegistryService registry = mock(RagBranchIndexRegistryService.class);
        ScheduledExecutorService executor = mock(ScheduledExecutorService.class);
        @SuppressWarnings("unchecked")
        ScheduledFuture<Object> scheduled = mock(ScheduledFuture.class);
        doReturn(scheduled).when(executor).scheduleAtFixedRate(
                any(Runnable.class), anyLong(), anyLong(), any());
        doThrow(new IllegalStateException("database unavailable"))
                .when(registry).heartbeatBuild(92L);
        RagIndexOperationHeartbeatService service =
                new RagIndexOperationHeartbeatService(registry, executor, 15L);

        service.start(92L);

        var heartbeat = org.mockito.ArgumentCaptor.forClass(Runnable.class);
        verify(executor).scheduleAtFixedRate(
                heartbeat.capture(), anyLong(), anyLong(), any());
        heartbeat.getValue().run();
        verify(registry).heartbeatBuild(92L);
    }
}
