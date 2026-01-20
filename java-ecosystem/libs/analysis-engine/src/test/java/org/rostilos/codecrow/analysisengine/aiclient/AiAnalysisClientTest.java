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
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.springframework.http.HttpMethod;
import org.springframework.http.client.ClientHttpRequestInterceptor;
import org.springframework.web.client.RequestCallback;
import org.springframework.web.client.ResponseExtractor;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.io.IOException;
import java.lang.reflect.Field;
import java.security.GeneralSecurityException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("AiAnalysisClient")
class AiAnalysisClientTest {

    @Mock
    private RestTemplate restTemplate;

    @Mock
    private AiAnalysisRequest mockRequest;

    private AiAnalysisClient client;

    private static final String AI_CLIENT_URL = "http://localhost:8000/review";

    @BeforeEach
    void setUp() throws Exception {
        when(restTemplate.getInterceptors()).thenReturn(new ArrayList<>());
        client = new AiAnalysisClient(restTemplate);
        setField(client, "aiClientUrl", AI_CLIENT_URL);
    }

    private void setField(Object target, String fieldName, Object value) throws Exception {
        Field field = target.getClass().getDeclaredField(fieldName);
        field.setAccessible(true);
        field.set(target, value);
    }

    @Nested
    @DisplayName("performAnalysis() without event handler")
    class PerformAnalysisWithoutEventHandlerTests {

        @Test
        @DisplayName("should successfully perform analysis with streaming response")
        void shouldSuccessfullyPerformAnalysisWithStreamingResponse() throws IOException, GeneralSecurityException {
            Map<String, Object> result = new HashMap<>();
            result.put("comment", "Code review comment");
            result.put("issues", List.of(
                    Map.of("line", 10, "message", "Consider using const"),
                    Map.of("line", 20, "message", "Missing null check")
            ));

            when(restTemplate.execute(
                    eq(AI_CLIENT_URL),
                    eq(HttpMethod.POST),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(result);

            Map<String, Object> response = client.performAnalysis(mockRequest);

            assertThat(response).containsKey("comment");
            assertThat(response).containsKey("issues");
            assertThat(response.get("comment")).isEqualTo("Code review comment");
            assertThat(response.get("issues")).isInstanceOf(List.class);
            assertThat((List<?>) response.get("issues")).hasSize(2);
        }

        @Test
        @DisplayName("should handle response with nested result structure")
        void shouldHandleResponseWithNestedResultStructure() throws IOException, GeneralSecurityException {
            Map<String, Object> innerResult = new HashMap<>();
            innerResult.put("comment", "Nested comment");
            innerResult.put("issues", List.of());

            Map<String, Object> response = new HashMap<>();
            response.put("result", innerResult);

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(response);

            Map<String, Object> result = client.performAnalysis(mockRequest);

            assertThat(result).containsKey("comment");
            assertThat(result.get("comment")).isEqualTo("Nested comment");
        }

        @Test
        @DisplayName("should handle issues as Map format")
        void shouldHandleIssuesAsMapFormat() throws IOException, GeneralSecurityException {
            Map<String, Object> issues = new HashMap<>();
            issues.put("0", Map.of("line", 1, "message", "Issue 1"));
            issues.put("1", Map.of("line", 2, "message", "Issue 2"));

            Map<String, Object> result = new HashMap<>();
            result.put("comment", "Comment");
            result.put("issues", issues);

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(result);

            Map<String, Object> response = client.performAnalysis(mockRequest);

            assertThat(response).containsKey("issues");
            assertThat(response.get("issues")).isInstanceOf(Map.class);
        }

        @Test
        @DisplayName("should fallback to postForObject when streaming returns null")
        void shouldFallbackToPostForObjectWhenStreamingReturnsNull() throws IOException, GeneralSecurityException {
            Map<String, Object> result = new HashMap<>();
            result.put("comment", "Fallback comment");
            result.put("issues", List.of());

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(null);

            when(restTemplate.postForObject(eq(AI_CLIENT_URL), any(), eq(Map.class)))
                    .thenReturn(result);

            Map<String, Object> response = client.performAnalysis(mockRequest);

            assertThat(response.get("comment")).isEqualTo("Fallback comment");
            verify(restTemplate).postForObject(eq(AI_CLIENT_URL), any(), eq(Map.class));
        }

        @Test
        @DisplayName("should throw IOException when fallback also returns null")
        void shouldThrowIOExceptionWhenFallbackReturnsNull() {
            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(null);

            when(restTemplate.postForObject(anyString(), any(), eq(Map.class)))
                    .thenReturn(null);

            assertThatThrownBy(() -> client.performAnalysis(mockRequest))
                    .isInstanceOf(IOException.class)
                    .hasMessage("AI service returned null response");
        }

        @Test
        @DisplayName("should fallback to postForObject when streaming throws RestClientException")
        void shouldFallbackWhenStreamingThrowsRestClientException() throws IOException, GeneralSecurityException {
            Map<String, Object> result = new HashMap<>();
            result.put("comment", "Recovered comment");
            result.put("issues", List.of());

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenThrow(new RestClientException("Stream error"));

            when(restTemplate.postForObject(anyString(), any(), eq(Map.class)))
                    .thenReturn(result);

            Map<String, Object> response = client.performAnalysis(mockRequest);

            assertThat(response.get("comment")).isEqualTo("Recovered comment");
        }

        @Test
        @DisplayName("should throw IOException when both streaming and fallback fail")
        void shouldThrowIOExceptionWhenBothAttemptsFail() {
            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenThrow(new RestClientException("Stream error"));

            when(restTemplate.postForObject(anyString(), any(), eq(Map.class)))
                    .thenReturn(null);

            assertThatThrownBy(() -> client.performAnalysis(mockRequest))
                    .isInstanceOf(IOException.class)
                    .hasMessage("AI service returned null response");
        }

        @Test
        @DisplayName("should throw IOException when outer RestClientException occurs")
        void shouldThrowIOExceptionOnOuterRestClientException() {
            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenThrow(new RestClientException("Outer error"));

            when(restTemplate.postForObject(anyString(), any(), eq(Map.class)))
                    .thenThrow(new RestClientException("Fallback error"));

            assertThatThrownBy(() -> client.performAnalysis(mockRequest))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("AI service communication failed");
        }
    }

    @Nested
    @DisplayName("performAnalysis() with event handler")
    class PerformAnalysisWithEventHandlerTests {

        @Test
        @DisplayName("should pass event handler to streaming execution")
        void shouldPassEventHandlerToStreamingExecution() throws IOException, GeneralSecurityException {
            List<Map<String, Object>> capturedEvents = new ArrayList<>();
            Consumer<Map<String, Object>> eventHandler = capturedEvents::add;

            Map<String, Object> result = new HashMap<>();
            result.put("comment", "Comment");
            result.put("issues", List.of());

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(result);

            client.performAnalysis(mockRequest, eventHandler);

            verify(restTemplate).execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            );
        }

        @Test
        @DisplayName("should work with null event handler")
        void shouldWorkWithNullEventHandler() throws IOException, GeneralSecurityException {
            Map<String, Object> result = new HashMap<>();
            result.put("comment", "Comment");
            result.put("issues", List.of());

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(result);

            Map<String, Object> response = client.performAnalysis(mockRequest, null);

            assertThat(response).containsKey("comment");
        }
    }

    @Nested
    @DisplayName("extractAndValidateAnalysisData()")
    class ExtractAndValidateAnalysisDataTests {

        @Test
        @DisplayName("should throw IOException when result field is missing")
        void shouldThrowIOExceptionWhenResultFieldIsMissing() {
            Map<String, Object> invalidResponse = new HashMap<>();
            invalidResponse.put("someOtherField", "value");

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(invalidResponse);

            assertThatThrownBy(() -> client.performAnalysis(mockRequest))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("Missing 'result' field");
        }

        @Test
        @DisplayName("should throw IOException when comment field is missing")
        void shouldThrowIOExceptionWhenCommentFieldIsMissing() {
            Map<String, Object> invalidResult = new HashMap<>();
            invalidResult.put("issues", List.of());

            Map<String, Object> response = new HashMap<>();
            response.put("result", invalidResult);

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(response);

            assertThatThrownBy(() -> client.performAnalysis(mockRequest))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("missing required fields");
        }

        @Test
        @DisplayName("should throw IOException when issues field is missing")
        void shouldThrowIOExceptionWhenIssuesFieldIsMissing() {
            Map<String, Object> invalidResult = new HashMap<>();
            invalidResult.put("comment", "Comment");

            Map<String, Object> response = new HashMap<>();
            response.put("result", invalidResult);

            when(restTemplate.execute(
                    anyString(),
                    any(HttpMethod.class),
                    any(RequestCallback.class),
                    any(ResponseExtractor.class)
            )).thenReturn(response);

            assertThatThrownBy(() -> client.performAnalysis(mockRequest))
                    .isInstanceOf(IOException.class)
                    .hasMessageContaining("missing required fields");
        }
    }

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should add Accept header interceptor")
        void shouldAddAcceptHeaderInterceptor() {
            List<ClientHttpRequestInterceptor> interceptors = new ArrayList<>();
            when(restTemplate.getInterceptors()).thenReturn(interceptors);

            new AiAnalysisClient(restTemplate);

            assertThat(interceptors).hasSize(1);
        }
    }
}
