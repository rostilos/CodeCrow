package org.rostilos.codecrow.webserver.dto.request.ai;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class UpdateAiConnectionRequest {
    public AIProviderKey providerKey;
    public String aiModel;
    public String apiKey;
    public String tokenLimitation;
}
