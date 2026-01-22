package org.rostilos.codecrow.core.dto.ai;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;

import java.lang.reflect.Field;
import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AIConnectionDTO")
class AIConnectionDTOTest {

    @Nested
    @DisplayName("record constructor")
    class RecordConstructorTests {

        @Test
        @DisplayName("should create AIConnectionDTO with all fields")
        void shouldCreateWithAllFields() {
            OffsetDateTime now = OffsetDateTime.now();
            AIConnectionDTO dto = new AIConnectionDTO(
                    1L, "Test Connection", AIProviderKey.ANTHROPIC, "claude-3-opus",
                    now, now, 100000
            );

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.name()).isEqualTo("Test Connection");
            assertThat(dto.providerKey()).isEqualTo(AIProviderKey.ANTHROPIC);
            assertThat(dto.aiModel()).isEqualTo("claude-3-opus");
            assertThat(dto.createdAt()).isEqualTo(now);
            assertThat(dto.updatedAt()).isEqualTo(now);
            assertThat(dto.tokenLimitation()).isEqualTo(100000);
        }

        @Test
        @DisplayName("should create AIConnectionDTO with null optional fields")
        void shouldCreateWithNullOptionalFields() {
            AIConnectionDTO dto = new AIConnectionDTO(
                    1L, null, AIProviderKey.OPENAI, null, null, null, 50000
            );

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.name()).isNull();
            assertThat(dto.aiModel()).isNull();
            assertThat(dto.createdAt()).isNull();
            assertThat(dto.updatedAt()).isNull();
        }

        @Test
        @DisplayName("should create AIConnectionDTO with different providers")
        void shouldCreateWithDifferentProviders() {
            AIConnectionDTO openai = new AIConnectionDTO(1L, "OpenAI", AIProviderKey.OPENAI, "gpt-4", null, null, 100000);
            AIConnectionDTO anthropic = new AIConnectionDTO(2L, "Anthropic", AIProviderKey.ANTHROPIC, "claude-3", null, null, 200000);
            AIConnectionDTO google = new AIConnectionDTO(3L, "Google", AIProviderKey.GOOGLE, "gemini-pro", null, null, 150000);

            assertThat(openai.providerKey()).isEqualTo(AIProviderKey.OPENAI);
            assertThat(anthropic.providerKey()).isEqualTo(AIProviderKey.ANTHROPIC);
            assertThat(google.providerKey()).isEqualTo(AIProviderKey.GOOGLE);
        }

        @Test
        @DisplayName("should support different token limitations")
        void shouldSupportDifferentTokenLimitations() {
            AIConnectionDTO small = new AIConnectionDTO(1L, "Small", AIProviderKey.OPENAI, "gpt-3.5", null, null, 16000);
            AIConnectionDTO large = new AIConnectionDTO(2L, "Large", AIProviderKey.ANTHROPIC, "claude-3", null, null, 200000);

            assertThat(small.tokenLimitation()).isEqualTo(16000);
            assertThat(large.tokenLimitation()).isEqualTo(200000);
        }
    }

    @Nested
    @DisplayName("fromAiConnection()")
    class FromAiConnectionTests {

        @Test
        @DisplayName("should convert AIConnection with all fields")
        void shouldConvertWithAllFields() {
            AIConnection connection = new AIConnection();
            setField(connection, "id", 1L);
            connection.setName("Production AI");
            setField(connection, "providerKey", AIProviderKey.ANTHROPIC);
            setField(connection, "aiModel", "claude-3-opus");
            setField(connection, "tokenLimitation", 100000);

            AIConnectionDTO dto = AIConnectionDTO.fromAiConnection(connection);

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.name()).isEqualTo("Production AI");
            assertThat(dto.providerKey()).isEqualTo(AIProviderKey.ANTHROPIC);
            assertThat(dto.aiModel()).isEqualTo("claude-3-opus");
            assertThat(dto.tokenLimitation()).isEqualTo(100000);
        }

        @Test
        @DisplayName("should convert AIConnection with null name")
        void shouldConvertWithNullName() {
            AIConnection connection = new AIConnection();
            setField(connection, "id", 2L);
            connection.setName(null);
            setField(connection, "providerKey", AIProviderKey.OPENAI);
            setField(connection, "aiModel", "gpt-4");
            setField(connection, "tokenLimitation", 50000);

            AIConnectionDTO dto = AIConnectionDTO.fromAiConnection(connection);

            assertThat(dto.name()).isNull();
            assertThat(dto.providerKey()).isEqualTo(AIProviderKey.OPENAI);
        }

        @Test
        @DisplayName("should convert AIConnection with null model")
        void shouldConvertWithNullModel() {
            AIConnection connection = new AIConnection();
            setField(connection, "id", 3L);
            connection.setName("Test");
            setField(connection, "providerKey", AIProviderKey.GOOGLE);
            setField(connection, "aiModel", null);
            setField(connection, "tokenLimitation", 75000);

            AIConnectionDTO dto = AIConnectionDTO.fromAiConnection(connection);

            assertThat(dto.aiModel()).isNull();
        }

        @Test
        @DisplayName("should convert all provider types")
        void shouldConvertAllProviderTypes() {
            for (AIProviderKey providerKey : AIProviderKey.values()) {
                AIConnection connection = new AIConnection();
                setField(connection, "id", 1L);
                setField(connection, "providerKey", providerKey);
                setField(connection, "tokenLimitation", 100000);

                AIConnectionDTO dto = AIConnectionDTO.fromAiConnection(connection);

                assertThat(dto.providerKey()).isEqualTo(providerKey);
            }
        }

        @Test
        @DisplayName("should handle timestamps")
        void shouldHandleTimestamps() {
            AIConnection connection = new AIConnection();
            setField(connection, "id", 1L);
            connection.setName("Test");
            setField(connection, "providerKey", AIProviderKey.ANTHROPIC);
            setField(connection, "tokenLimitation", 100000);

            AIConnectionDTO dto = AIConnectionDTO.fromAiConnection(connection);

            assertThat(dto.createdAt()).isNotNull();
        }
    }

    @Nested
    @DisplayName("equality and hashCode")
    class EqualityTests {

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            OffsetDateTime now = OffsetDateTime.now();
            AIConnectionDTO dto1 = new AIConnectionDTO(1L, "Test", AIProviderKey.OPENAI, "gpt-4", now, now, 100000);
            AIConnectionDTO dto2 = new AIConnectionDTO(1L, "Test", AIProviderKey.OPENAI, "gpt-4", now, now, 100000);

            assertThat(dto1).isEqualTo(dto2);
            assertThat(dto1.hashCode()).isEqualTo(dto2.hashCode());
        }

        @Test
        @DisplayName("should not be equal for different values")
        void shouldNotBeEqualForDifferentValues() {
            OffsetDateTime now = OffsetDateTime.now();
            AIConnectionDTO dto1 = new AIConnectionDTO(1L, "Test1", AIProviderKey.OPENAI, "gpt-4", now, now, 100000);
            AIConnectionDTO dto2 = new AIConnectionDTO(2L, "Test2", AIProviderKey.ANTHROPIC, "claude", now, now, 200000);

            assertThat(dto1).isNotEqualTo(dto2);
        }
    }

    private void setField(Object obj, String fieldName, Object value) {
        try {
            Field field = obj.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(obj, value);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set field " + fieldName, e);
        }
    }
}
