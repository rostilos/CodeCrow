package org.rostilos.codecrow.core.dto.ai;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;

import java.time.OffsetDateTime;

public record AIConnectionDTO(
        Long id,
        String name,
        AIProviderKey providerKey,
        String aiModel,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt,
        int tokenLimitation
) {

    public static AIConnectionDTO fromAiConnection(AIConnection aiConnection) {
        return new AIConnectionDTO(
                aiConnection.getId(),
                aiConnection.getName(),
                aiConnection.getProviderKey(),
                aiConnection.getAiModel(),
                aiConnection.getCreatedAt(),
                aiConnection.getUpdatedAt(),
                aiConnection.getTokenLimitation()
        );
    }
}
