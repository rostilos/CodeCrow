package org.rostilos.codecrow.webserver.ai.dto.response;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.ai.LlmModel;

import java.time.OffsetDateTime;

public record LlmModelDTO(
        Long id,
        AIProviderKey providerKey,
        String modelId,
        String displayName,
        Integer contextWindow,
        boolean supportsTools,
        String inputPricePerMillion,
        String outputPricePerMillion,
        OffsetDateTime lastSyncedAt
) {
    public static LlmModelDTO from(LlmModel model) {
        return new LlmModelDTO(
                model.getId(),
                model.getProviderKey(),
                model.getModelId(),
                model.getDisplayName(),
                model.getContextWindow(),
                model.isSupportsTools(),
                model.getInputPricePerMillion(),
                model.getOutputPricePerMillion(),
                model.getLastSyncedAt()
        );
    }
}
