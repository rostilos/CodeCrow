package org.rostilos.codecrow.core.model.ai;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

@DisplayName("AIConnection Entity Tests")
class AIConnectionTest {

    private AIConnection aiConnection;

    @BeforeEach
    void setUp() {
        aiConnection = new AIConnection();
    }

    @Nested
    @DisplayName("Getter and Setter tests")
    class GetterSetterTests {

        @Test
        @DisplayName("Should set and get name")
        void shouldSetAndGetName() {
            aiConnection.setName("My AI Connection");
            assertThat(aiConnection.getName()).isEqualTo("My AI Connection");
        }

        @Test
        @DisplayName("Should set and get workspace")
        void shouldSetAndGetWorkspace() {
            Workspace workspace = mock(Workspace.class);
            aiConnection.setWorkspace(workspace);
            assertThat(aiConnection.getWorkspace()).isSameAs(workspace);
        }

        @Test
        @DisplayName("Should set and get providerKey")
        void shouldSetAndGetProviderKey() {
            aiConnection.setProviderKey(AIProviderKey.OPENAI);
            assertThat(aiConnection.getProviderKey()).isEqualTo(AIProviderKey.OPENAI);
            
            aiConnection.setProviderKey(AIProviderKey.ANTHROPIC);
            assertThat(aiConnection.getProviderKey()).isEqualTo(AIProviderKey.ANTHROPIC);
            
            aiConnection.setProviderKey(AIProviderKey.GOOGLE);
            assertThat(aiConnection.getProviderKey()).isEqualTo(AIProviderKey.GOOGLE);
            
            aiConnection.setProviderKey(AIProviderKey.OPENROUTER);
            assertThat(aiConnection.getProviderKey()).isEqualTo(AIProviderKey.OPENROUTER);
        }

        @Test
        @DisplayName("Should set and get aiModel")
        void shouldSetAndGetAiModel() {
            aiConnection.setAiModel("gpt-4-turbo");
            assertThat(aiConnection.getAiModel()).isEqualTo("gpt-4-turbo");
        }

        @Test
        @DisplayName("Should set and get apiKeyEncrypted")
        void shouldSetAndGetApiKeyEncrypted() {
            aiConnection.setApiKeyEncrypted("encrypted-api-key-xyz");
            assertThat(aiConnection.getApiKeyEncrypted()).isEqualTo("encrypted-api-key-xyz");
        }

        @Test
        @DisplayName("Should set and get tokenLimitation")
        void shouldSetAndGetTokenLimitation() {
            aiConnection.setTokenLimitation(50000);
            assertThat(aiConnection.getTokenLimitation()).isEqualTo(50000);
        }
    }

    @Nested
    @DisplayName("Default value tests")
    class DefaultValueTests {

        @Test
        @DisplayName("Default tokenLimitation should be 100000")
        void defaultTokenLimitationShouldBe100000() {
            assertThat(aiConnection.getTokenLimitation()).isEqualTo(100000);
        }

        @Test
        @DisplayName("Id should be null for new entity")
        void idShouldBeNullForNewEntity() {
            assertThat(aiConnection.getId()).isNull();
        }

        @Test
        @DisplayName("CreatedAt should be set automatically")
        void createdAtShouldBeSetAutomatically() {
            assertThat(aiConnection.getCreatedAt()).isNotNull();
        }

        @Test
        @DisplayName("UpdatedAt should be set automatically")
        void updatedAtShouldBeSetAutomatically() {
            assertThat(aiConnection.getUpdatedAt()).isNotNull();
        }
    }

    @Nested
    @DisplayName("Initial state tests")
    class InitialStateTests {

        @Test
        @DisplayName("New AIConnection should have null name")
        void newAiConnectionShouldHaveNullName() {
            assertThat(aiConnection.getName()).isNull();
        }

        @Test
        @DisplayName("New AIConnection should have null workspace")
        void newAiConnectionShouldHaveNullWorkspace() {
            assertThat(aiConnection.getWorkspace()).isNull();
        }

        @Test
        @DisplayName("New AIConnection should have null providerKey")
        void newAiConnectionShouldHaveNullProviderKey() {
            assertThat(aiConnection.getProviderKey()).isNull();
        }

        @Test
        @DisplayName("New AIConnection should have null aiModel")
        void newAiConnectionShouldHaveNullAiModel() {
            assertThat(aiConnection.getAiModel()).isNull();
        }

        @Test
        @DisplayName("New AIConnection should have null apiKeyEncrypted")
        void newAiConnectionShouldHaveNullApiKeyEncrypted() {
            assertThat(aiConnection.getApiKeyEncrypted()).isNull();
        }
    }

    @Nested
    @DisplayName("Update tests")
    class UpdateTests {

        @Test
        @DisplayName("Should be able to update all fields")
        void shouldBeAbleToUpdateAllFields() {
            Workspace workspace = mock(Workspace.class);
            
            aiConnection.setName("Updated Name");
            aiConnection.setWorkspace(workspace);
            aiConnection.setProviderKey(AIProviderKey.ANTHROPIC);
            aiConnection.setAiModel("claude-3-opus");
            aiConnection.setApiKeyEncrypted("new-encrypted-key");
            aiConnection.setTokenLimitation(200000);
            
            assertThat(aiConnection.getName()).isEqualTo("Updated Name");
            assertThat(aiConnection.getWorkspace()).isSameAs(workspace);
            assertThat(aiConnection.getProviderKey()).isEqualTo(AIProviderKey.ANTHROPIC);
            assertThat(aiConnection.getAiModel()).isEqualTo("claude-3-opus");
            assertThat(aiConnection.getApiKeyEncrypted()).isEqualTo("new-encrypted-key");
            assertThat(aiConnection.getTokenLimitation()).isEqualTo(200000);
        }

        @Test
        @DisplayName("Should handle null values on update")
        void shouldHandleNullValuesOnUpdate() {
            aiConnection.setName("Name");
            aiConnection.setAiModel("model");
            
            aiConnection.setName(null);
            aiConnection.setAiModel(null);
            aiConnection.setWorkspace(null);
            
            assertThat(aiConnection.getName()).isNull();
            assertThat(aiConnection.getAiModel()).isNull();
            assertThat(aiConnection.getWorkspace()).isNull();
        }
    }
}
