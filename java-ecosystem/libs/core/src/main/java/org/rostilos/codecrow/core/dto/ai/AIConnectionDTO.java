package org.rostilos.codecrow.core.dto.ai;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;

import java.time.OffsetDateTime;

public record AIConnectionDTO(
        Long id,
        String name,
        AIProviderKey providerKey,
        String aiModel,
        String baseUrl,
        String customParameters,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {

    public AIConnectionDTO(
            Long id,
            String name,
            AIProviderKey providerKey,
            String aiModel,
            String baseUrl,
            OffsetDateTime createdAt,
            OffsetDateTime updatedAt
    ) {
        this(id, name, providerKey, aiModel, baseUrl, null, createdAt, updatedAt);
    }

    public static AIConnectionDTO fromAiConnection(AIConnection aiConnection) {
        return new AIConnectionDTO(
                aiConnection.getId(),
                aiConnection.getName(),
                aiConnection.getProviderKey(),
                aiConnection.getAiModel(),
                aiConnection.getBaseUrl(),
                aiConnection.getCustomParameters(),
                aiConnection.getCreatedAt(),
                aiConnection.getUpdatedAt()
        );
    }
}
