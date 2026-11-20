package org.rostilos.codecrow.webserver.ai.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.ai.*;
import org.rostilos.codecrow.core.persistence.repository.ai.LlmModelRepository;
import org.rostilos.codecrow.webserver.ai.provider.LlmModelFetcher;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class LlmModelSyncServiceTest {
    private final LlmModelRepository repository = mock(LlmModelRepository.class);
    private final LlmModelFetcher fetcher = mock(LlmModelFetcher.class);
    private final LlmModelSyncService service = new LlmModelSyncService(List.of(fetcher), repository);

    private void configure() {
        when(fetcher.getProviderKey()).thenReturn(AIProviderKey.OPENROUTER);
        when(fetcher.isConfigured()).thenReturn(true);
    }

    @Test
    void failedOrEmptyFetchNeverDeletesCachedModels() {
        configure();
        when(fetcher.fetchModels(anyInt())).thenThrow(new IllegalStateException("offline"));
        assertThat(service.syncAllProviders().hasErrors()).isTrue();
        doReturn(List.of()).when(fetcher).fetchModels(anyInt());
        service.syncAllProviders();
        verify(repository, never()).deleteStaleModels(any(), any());
    }

    @Test
    void preservesFailedProviderRefreshThenReplacesSnapshotOnSuccess() {
        configure();
        var old = new LlmModel();
        var timestamp = OffsetDateTime.now().minusDays(1);
        old.setProviders(List.of(new LlmModel.ModelProvider("old", "Old")));
        old.setProvidersSyncedAt(timestamp);
        var fresh = new LlmModel();
        fresh.setProviderKey(AIProviderKey.OPENROUTER);
        fresh.setModelId("test/model");
        fresh.setLastSyncedAt(OffsetDateTime.now());
        when(repository.findByProviderKeyAndModelId(AIProviderKey.OPENROUTER, "test/model"))
                .thenReturn(Optional.of(old));
        when(fetcher.fetchModels(anyInt())).thenReturn(List.of(fresh));
        service.syncAllProviders();
        assertThat(old.getProviders()).extracting("slug").containsExactly("old");
        assertThat(old.getProvidersSyncedAt()).isEqualTo(timestamp);
        fresh.setProviders(List.of(new LlmModel.ModelProvider("new", "New")));
        fresh.setProvidersSyncedAt(OffsetDateTime.now());
        service.syncAllProviders();
        assertThat(old.getProviders()).extracting("slug").containsExactly("new");
        verify(repository, times(2)).deleteStaleModels(eq(AIProviderKey.OPENROUTER), any());
        verify(repository, never()).deleteStaleModels(eq(AIProviderKey.OPENAI), any());
    }
}
