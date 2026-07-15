package org.rostilos.codecrow.webserver.ai.scheduler;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.webserver.ai.service.LlmModelSyncService;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class LlmModelSyncSchedulerTest {

    @Test
    void scheduledSyncReportsSuccessfulAndPartiallyFailedResults() {
        LlmModelSyncService service = mock(LlmModelSyncService.class);
        when(service.syncAllProviders())
                .thenReturn(result(Map.of(), 2))
                .thenReturn(result(Map.of(AIProviderKey.OPENROUTER, "failed"), 0));
        LlmModelSyncScheduler scheduler = new LlmModelSyncScheduler(service);

        assertThatCode(scheduler::scheduledSync).doesNotThrowAnyException();
        assertThatCode(scheduler::scheduledSync).doesNotThrowAnyException();

        verify(service, org.mockito.Mockito.times(2)).syncAllProviders();
    }

    @Test
    void scheduledAndStartupFailuresRemainContained() {
        LlmModelSyncService service = mock(LlmModelSyncService.class);
        when(service.syncAllProviders()).thenThrow(new IllegalStateException("offline failure"));
        LlmModelSyncScheduler scheduler = new LlmModelSyncScheduler(service);

        assertThatCode(scheduler::scheduledSync).doesNotThrowAnyException();
        assertThatCode(scheduler::onApplicationReady).doesNotThrowAnyException();
    }

    @Test
    void startupSuccessInvokesTheSameBoundedSyncService() {
        LlmModelSyncService service = mock(LlmModelSyncService.class);
        when(service.syncAllProviders()).thenReturn(result(Map.of(), 0));

        assertThatCode(() -> new LlmModelSyncScheduler(service).onApplicationReady())
                .doesNotThrowAnyException();
        verify(service).syncAllProviders();
    }

    private static LlmModelSyncService.SyncResult result(
            Map<AIProviderKey, String> errors, int cleanedUp
    ) {
        return new LlmModelSyncService.SyncResult(
                Map.of(AIProviderKey.OPENROUTER, 3), errors, cleanedUp
        );
    }
}
