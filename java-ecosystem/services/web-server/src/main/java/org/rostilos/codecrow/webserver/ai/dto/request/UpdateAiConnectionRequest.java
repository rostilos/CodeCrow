package org.rostilos.codecrow.webserver.ai.dto.request;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class UpdateAiConnectionRequest {
    public String name;
    public AIProviderKey providerKey;
    public String aiModel;
    public String apiKey;
    public String tokenLimitation;
}
