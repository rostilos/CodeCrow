package org.rostilos.codecrow.webserver.ai.dto.request;

import com.fasterxml.jackson.annotation.JsonInclude;
import jakarta.validation.constraints.NotBlank;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class CreateAIConnectionRequest {
    public String name;
    @NotBlank(message = "Provider key is required")
    public AIProviderKey providerKey;
    @NotBlank(message = "Ai Model is required")
    public String aiModel;
    @NotBlank(message = "API key is required")
    public String apiKey;
    @NotBlank(message = "Please specify max token limit")
    public String tokenLimitation;
}
