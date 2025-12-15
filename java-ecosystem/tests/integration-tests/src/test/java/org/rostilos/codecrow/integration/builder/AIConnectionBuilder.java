package org.rostilos.codecrow.integration.builder;

import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.workspace.Workspace;

/**
 * Builder for creating AI connection test data.
 */
public class AIConnectionBuilder {
    
    private String name = "Test AI Connection";
    private Workspace workspace;
    private AIProviderKey providerKey = AIProviderKey.OPENROUTER;
    private String aiModel = "anthropic/claude-3-haiku";
    private String apiKeyEncrypted = "encrypted-test-key";
    private int tokenLimitation = 200000;

    public static AIConnectionBuilder anAIConnection() {
        return new AIConnectionBuilder();
    }

    public AIConnectionBuilder withName(String name) {
        this.name = name;
        return this;
    }

    public AIConnectionBuilder withWorkspace(Workspace workspace) {
        this.workspace = workspace;
        return this;
    }

    public AIConnectionBuilder withProviderKey(AIProviderKey providerKey) {
        this.providerKey = providerKey;
        return this;
    }

    public AIConnectionBuilder withAiModel(String aiModel) {
        this.aiModel = aiModel;
        return this;
    }

    public AIConnectionBuilder withApiKeyEncrypted(String apiKeyEncrypted) {
        this.apiKeyEncrypted = apiKeyEncrypted;
        return this;
    }

    public AIConnectionBuilder withTokenLimitation(int tokenLimitation) {
        this.tokenLimitation = tokenLimitation;
        return this;
    }

    public AIConnectionBuilder openAI() {
        this.providerKey = AIProviderKey.OPENAI;
        this.aiModel = "gpt-4o-mini";
        return this;
    }

    public AIConnectionBuilder anthropic() {
        this.providerKey = AIProviderKey.ANTHROPIC;
        this.aiModel = "claude-3-haiku-20240307";
        return this;
    }

    public AIConnectionBuilder openRouter() {
        this.providerKey = AIProviderKey.OPENROUTER;
        this.aiModel = "anthropic/claude-3-haiku";
        return this;
    }

    public AIConnection build() {
        AIConnection connection = new AIConnection();
        connection.setName(name);
        connection.setWorkspace(workspace);
        connection.setProviderKey(providerKey);
        connection.setAiModel(aiModel);
        connection.setApiKeyEncrypted(apiKeyEncrypted);
        connection.setTokenLimitation(tokenLimitation);
        return connection;
    }
}
