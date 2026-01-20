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
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.ClientHttpRequest;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.web.client.RequestCallback;
import org.springframework.web.client.ResponseExtractor;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("AiCommandClient")
class AiCommandClientTest {

    @Mock
    private RestTemplate restTemplate;

    private AiCommandClient client;

    private static final String BASE_URL = "http://localhost:8000";

    @BeforeEach
    void setUp() throws Exception {
        client = new AiCommandClient(restTemplate);
        setField(client, "aiClientBaseUrl", BASE_URL);
    }

    private void setField(Object target, String fieldName, Object value) throws Exception {
        Field field = target.getClass().getDeclaredField(fieldName);
        field.setAccessible(true);
        field.set(target, value);
    }

    private AiCommandClient.SummarizeRequest createSummarizeRequest() {
        return new AiCommandClient.SummarizeRequest(
                1L, "workspace", "repo-slug", "project-workspace", "namespace",
                "openai", "gpt-4", "api-key", 42L, "feature", "main", "abc123",
                "oauth-client", "oauth-secret", "access-token", true, 4096, "bitbucket"
        );
    }

    private AiCommandClient.AskRequest createAskRequest() {
        return new AiCommandClient.AskRequest(
                1L, "workspace", "repo-slug", "project-workspace", "namespace",
                "openai", "gpt-4", "api-key", "What is this code doing?",
                42L, "abc123", "oauth-client", "oauth-secret", "access-token",
                4096, "bitbucket", null, null
        );
    }

    private AiCommandClient.ReviewRequest createReviewRequest() {
        return new AiCommandClient.ReviewRequest(
                1L, "workspace", "repo-slug", "project-workspace", "namespace",
                "openai", "gpt-4", "api-key", 42L, "feature", "main", "abc123",
                "oauth-client", "oauth-secret", "access-token", 4096, "bitbucket"
        );
    }

    @Nested
    @DisplayName("summarize()")
    class SummarizeTests {

        @Test
        @DisplayName("should successfully summarize PR")
        void shouldSuccessfullySummarizePR() throws IOException {
            Map<String, Object> successResponse = Map.of(
                    "summary", "This PR adds new features",
                    "diagram", "graph LR; A-->B",
                    "diagramType", "MERMAID"
            );

            when(restTemplate.execute(
                    eq(BASE_URL + "/summarize"),
                    eq(HttpMethod.POST),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(successResponse);

            AiCommandClient.SummarizeResult result = client.summarize(createSummarizeRequest(), null);

            assertThat(result.summary()).isEqualTo("This PR adds new features");
            assertThat(result.diagram()).isEqualTo("graph LR; A-->B");
            assertThat(result.diagramType()).isEqualTo("MERMAID");

            verify(restTemplate).execute(
                    eq(BASE_URL + "/summarize"),
                    eq(HttpMethod.POST),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            );
        }

        @Test
        @DisplayName("should throw IOException when response is null")
        void shouldThrowIOExceptionWhenResponseIsNull() {
            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(null);

            assertThatThrownBy(() -> client.summarize(createSummarizeRequest(), null))
                    .isInstanceOf(IOException.class)
                    .hasMessage("AI service returned null response");
        }

        @Test
        @DisplayName("should throw IOException when response contains error")
        void shouldThrowIOExceptionWhenResponseContainsError() {
            Map<String, Object> errorResponse = Map.of(
                    "error", "Rate limit exceeded"
            );

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(errorResponse);

            assertThatThrownBy(() -> client.summarize(createSummarizeRequest(), null))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("Summarize failed: Rate limit exceeded");
        }

        @Test
        @DisplayName("should throw IOException when RestClientException occurs")
        void shouldThrowIOExceptionOnRestClientException() {
            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenThrow(new RestClientException("Connection refused"));

            assertThatThrownBy(() -> client.summarize(createSummarizeRequest(), null))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("AI service communication failed");
        }

        @Test
        @DisplayName("should use default values for missing fields")
        void shouldUseDefaultValuesForMissingFields() throws IOException {
            Map<String, Object> partialResponse = Map.of();

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(partialResponse);

            AiCommandClient.SummarizeResult result = client.summarize(createSummarizeRequest(), null);

            assertThat(result.summary()).isEmpty();
            assertThat(result.diagram()).isEmpty();
            assertThat(result.diagramType()).isEqualTo("MERMAID");
        }
    }

    @Nested
    @DisplayName("ask()")
    class AskTests {

        @Test
        @DisplayName("should successfully answer question")
        void shouldSuccessfullyAnswerQuestion() throws IOException {
            Map<String, Object> successResponse = Map.of(
                    "answer", "This code implements a REST API endpoint"
            );

            when(restTemplate.execute(
                    eq(BASE_URL + "/ask"),
                    eq(HttpMethod.POST),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(successResponse);

            AiCommandClient.AskResult result = client.ask(createAskRequest(), null);

            assertThat(result.answer()).isEqualTo("This code implements a REST API endpoint");
        }

        @Test
        @DisplayName("should throw IOException when response is null")
        void shouldThrowIOExceptionWhenResponseIsNull() {
            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(null);

            assertThatThrownBy(() -> client.ask(createAskRequest(), null))
                    .isInstanceOf(IOException.class)
                    .hasMessage("AI service returned null response");
        }

        @Test
        @DisplayName("should throw IOException when response contains error")
        void shouldThrowIOExceptionWhenResponseContainsError() {
            Map<String, Object> errorResponse = Map.of(
                    "error", "Invalid question format"
            );

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(errorResponse);

            assertThatThrownBy(() -> client.ask(createAskRequest(), null))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("Ask failed: Invalid question format");
        }

        @Test
        @DisplayName("should throw IOException when RestClientException occurs")
        void shouldThrowIOExceptionOnRestClientException() {
            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenThrow(new RestClientException("Timeout"));

            assertThatThrownBy(() -> client.ask(createAskRequest(), null))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("AI service communication failed");
        }

        @Test
        @DisplayName("should use default empty answer for missing field")
        void shouldUseDefaultEmptyAnswerForMissingField() throws IOException {
            Map<String, Object> emptyResponse = Map.of();

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(emptyResponse);

            AiCommandClient.AskResult result = client.ask(createAskRequest(), null);

            assertThat(result.answer()).isEmpty();
        }
    }

    @Nested
    @DisplayName("review()")
    class ReviewTests {

        @Test
        @DisplayName("should successfully review code")
        void shouldSuccessfullyReviewCode() throws IOException {
            Map<String, Object> successResponse = Map.of(
                    "review", "## Code Review\n\nLooks good!"
            );

            when(restTemplate.execute(
                    eq(BASE_URL + "/review"),
                    eq(HttpMethod.POST),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(successResponse);

            AiCommandClient.ReviewResult result = client.review(createReviewRequest(), null);

            assertThat(result.review()).isEqualTo("## Code Review\n\nLooks good!");
        }

        @Test
        @DisplayName("should throw IOException when response is null")
        void shouldThrowIOExceptionWhenResponseIsNull() {
            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(null);

            assertThatThrownBy(() -> client.review(createReviewRequest(), null))
                    .isInstanceOf(IOException.class)
                    .hasMessage("AI service returned null response");
        }

        @Test
        @DisplayName("should throw IOException when response contains error")
        void shouldThrowIOExceptionWhenResponseContainsError() {
            Map<String, Object> errorResponse = Map.of(
                    "error", "Analysis timeout"
            );

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(errorResponse);

            assertThatThrownBy(() -> client.review(createReviewRequest(), null))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("Review failed: Analysis timeout");
        }

        @Test
        @DisplayName("should throw IOException when RestClientException occurs")
        void shouldThrowIOExceptionOnRestClientException() {
            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenThrow(new RestClientException("Service unavailable"));

            assertThatThrownBy(() -> client.review(createReviewRequest(), null))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("AI service communication failed");
        }

        @Test
        @DisplayName("should use default empty review for missing field")
        void shouldUseDefaultEmptyReviewForMissingField() throws IOException {
            Map<String, Object> emptyResponse = Map.of();

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(emptyResponse);

            AiCommandClient.ReviewResult result = client.review(createReviewRequest(), null);

            assertThat(result.review()).isEmpty();
        }
    }

    @Nested
    @DisplayName("NDJSON Streaming")
    class NdjsonStreamingTests {

        @Test
        @DisplayName("should handle null error in response")
        void shouldHandleNullErrorInResponse() throws IOException {
            // Response with error key but null value should not throw
            Map<String, Object> responseWithNullError = new java.util.HashMap<>();
            responseWithNullError.put("error", null);
            responseWithNullError.put("summary", "Test summary");
            responseWithNullError.put("diagram", "");
            responseWithNullError.put("diagramType", "MERMAID");

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(responseWithNullError);

            AiCommandClient.SummarizeResult result = client.summarize(createSummarizeRequest(), null);
            assertThat(result.summary()).isEqualTo("Test summary");
        }
    }

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should create client with provided RestTemplate")
        void shouldCreateClientWithProvidedRestTemplate() {
            AiCommandClient newClient = new AiCommandClient(restTemplate);
            assertThat(newClient).isNotNull();
        }
    }
}
