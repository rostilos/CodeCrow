package org.rostilos.codecrow.analysisengine.aiclient;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.queue.RedisQueueService;

import java.io.IOException;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("AiCommandClient")
class AiCommandClientTest {

        @Mock
        private RedisQueueService queueService;

        private ObjectMapper objectMapper;
        private AiCommandClient client;

        @BeforeEach
        void setUp() {
                objectMapper = new ObjectMapper();
                client = new AiCommandClient(queueService, objectMapper);
        }

        private AiCommandClient.SummarizeRequest createSummarizeRequest() {
                return new AiCommandClient.SummarizeRequest(
                                1L, "workspace", "repo-slug", "project-workspace", "namespace",
                                "openai", "gpt-4", "api-key", 42L, "feature", "main", "abc123",
                                "oauth-client", "oauth-secret", "access-token", true, 4096, "bitbucket");
        }

        private AiCommandClient.AskRequest createAskRequest() {
                return new AiCommandClient.AskRequest(
                                1L, "workspace", "repo-slug", "project-workspace", "namespace",
                                "openai", "gpt-4", "api-key", "What is this code doing?",
                                42L, "abc123", "oauth-client", "oauth-secret", "access-token",
                                4096, "bitbucket", null, null);
        }

        private AiCommandClient.ReviewRequest createReviewRequest() {
                return new AiCommandClient.ReviewRequest(
                                1L, "workspace", "repo-slug", "project-workspace", "namespace",
                                "openai", "gpt-4", "api-key", 42L, "feature", "main", "abc123",
                                "oauth-client", "oauth-secret", "access-token", 4096, "bitbucket");
        }

        @Nested
        @DisplayName("summarize()")
        class SummarizeTests {

                @Test
                @DisplayName("should successfully summarize PR")
                void shouldSuccessfullySummarizePR() throws Exception {
                        Map<String, Object> finalEvent = Map.of(
                                        "type", "final",
                                        "result", Map.of(
                                                        "summary", "This PR adds new features",
                                                        "diagram", "graph LR; A-->B",
                                                        "diagramType", "MERMAID"));

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(objectMapper.writeValueAsString(finalEvent));

                        AiCommandClient.SummarizeResult result = client.summarize(createSummarizeRequest(), null);

                        assertThat(result.summary()).isEqualTo("This PR adds new features");
                        assertThat(result.diagram()).isEqualTo("graph LR; A-->B");
                        assertThat(result.diagramType()).isEqualTo("MERMAID");

                        verify(queueService).leftPush(eq("codecrow:queue:commands"), anyString());
                        verify(queueService).setExpiry(anyString(), anyLong());
                        verify(queueService).deleteKey(anyString());
                }

                @Test
                @DisplayName("should throw IOException when queue times out or returns error event")
                void shouldThrowIOExceptionWhenErrorEvent() throws Exception {
                        Map<String, Object> errorEvent = Map.of(
                                        "type", "error",
                                        "message", "Rate limit exceeded");

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(objectMapper.writeValueAsString(errorEvent));

                        assertThatThrownBy(() -> client.summarize(createSummarizeRequest(), null))
                                        .isInstanceOf(IOException.class)
                                        .hasMessageContaining("AI service returned error: Rate limit exceeded");
                }
        }

        @Nested
        @DisplayName("ask()")
        class AskTests {

                @Test
                @DisplayName("should successfully answer question")
                void shouldSuccessfullyAnswerQuestion() throws Exception {
                        Map<String, Object> finalEvent = Map.of(
                                        "type", "final",
                                        "result", Map.of("answer", "This code implements a REST API endpoint"));

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(objectMapper.writeValueAsString(finalEvent));

                        AiCommandClient.AskResult result = client.ask(createAskRequest(), null);

                        assertThat(result.answer()).isEqualTo("This code implements a REST API endpoint");
                }
        }

        @Nested
        @DisplayName("review()")
        class ReviewTests {

                @Test
                @DisplayName("should successfully review code")
                void shouldSuccessfullyReviewCode() throws Exception {
                        Map<String, Object> finalEvent = Map.of(
                                        "type", "final",
                                        "result", Map.of("review", "## Code Review\n\nLooks good!"));

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(objectMapper.writeValueAsString(finalEvent));

                        AiCommandClient.ReviewResult result = client.review(createReviewRequest(), null);

                        assertThat(result.review()).isEqualTo("## Code Review\n\nLooks good!");
                }
        }
}
