package org.rostilos.codecrow.analysisengine.aiclient;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.ParsedFileMetadataDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.springframework.web.client.RestTemplate;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("AiAnalysisClient - Redis Queuing")
class AiAnalysisClientTest {

        @Mock
        private RestTemplate restTemplate;

        @Mock
        private RedisQueueService queueService;

        private ObjectMapper objectMapper;

        private AiAnalysisClient client;

        public static class TestAiAnalysisRequest implements AiAnalysisRequest {
                @Override
                public Long getProjectId() {
                        return 1L;
                }

                @Override
                public String getProjectVcsWorkspace() {
                        return "ws";
                }

                @Override
                public String getProjectWorkspace() {
                        return "Codecrow";
                }

                @Override
                public String getProjectNamespace() {
                        return "codecrow-garden";
                }

                @Override
                public String getProjectVcsRepoSlug() {
                        return "repo";
                }

                @Override
                public org.rostilos.codecrow.core.model.ai.AIProviderKey getAiProvider() {
                        return null;
                }

                @Override
                public String getAiModel() {
                        return "model";
                }

                @Override
                public String getAiApiKey() {
                        return "key";
                }

                @Override
                public Long getPullRequestId() {
                        return 2L;
                }

                @Override
                public String getOAuthClient() {
                        return "client";
                }

                @Override
                public String getOAuthSecret() {
                        return "secret";
                }

                @Override
                public String getAccessToken() {
                        return "token";
                }

                @Override
                public int getMaxAllowedTokens() {
                        return 1000;
                }

                @Override
                public boolean getUseLocalMcp() {
                        return false;
                }

                @Override
                public boolean getUseMcpTools() {
                        return false;
                }

                @Override
                public org.rostilos.codecrow.core.model.codeanalysis.AnalysisType getAnalysisType() {
                        return null;
                }

                @Override
                public String getVcsProvider() {
                        return "github";
                }

                @Override
                public String getPrTitle() {
                        return "title";
                }

                @Override
                public String getPrDescription() {
                        return "desc";
                }

                @Override
                public List<String> getChangedFiles() {
                        return List.of();
                }

                @Override
                public List<String> getDeletedFiles() {
                        return List.of();
                }

                @Override
                public List<String> getDiffSnippets() {
                        return List.of();
                }

                @Override
                public String getTargetBranchName() {
                        return "main";
                }

                @Override
                public String getSourceBranchName() {
                        return "feature/frontend/GR-2509";
                }

                @Override
                public String getRawDiff() {
                        return "diff";
                }

                @Override
                public org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode getAnalysisMode() {
                        return null;
                }

                @Override
                public String getDeltaDiff() {
                        return "";
                }

                @Override
                public String getPreviousCommitHash() {
                        return "";
                }

                @Override
                public String getCurrentCommitHash() {
                        return "";
                }

                @Override
                public Map<String, String> getReconciliationFileContents() {
                        return null;
                }
        }

        private AiAnalysisRequest mockRequest = new TestAiAnalysisRequest();

        @BeforeEach
        void setUp() throws Exception {
                objectMapper = new ObjectMapper();
                client = new AiAnalysisClient(restTemplate, queueService, objectMapper);
        }

        @Nested
        @DisplayName("performAnalysis() success paths")
        class PerformAnalysisSuccessTests {

                @Test
                @DisplayName("should successfully perform analysis by polling Redis")
                void shouldSuccessfullyPerformAnalysis() throws Exception {
                        Map<String, Object> finalEvent = new HashMap<>();
                        finalEvent.put("type", "final");

                        Map<String, Object> resultPayload = new HashMap<>();
                        resultPayload.put("comment", "Code review comment");
                        resultPayload.put("issues", List.of(
                                        Map.of("line", 10, "message", "Consider using const"),
                                        Map.of("line", 20, "message", "Missing null check")));
                        finalEvent.put("result", resultPayload);

                        String finalEventJson = objectMapper.writeValueAsString(finalEvent);

                        // Mock rightPop to return the final event immediately
                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(finalEventJson);

                        Map<String, Object> response = client.performAnalysis(mockRequest);

                        // Verify push
                        verify(queueService).leftPush(eq("codecrow:analysis:jobs"), anyString());
                        // Verify pop
                        verify(queueService).rightPop(anyString(), eq(5L));

                        assertThat(response).containsKey("comment");
                        assertThat(response).containsKey("issues");
                        assertThat(response.get("comment")).isEqualTo("Code review comment");
                        assertThat(response.get("issues")).isInstanceOf(List.class);
                        assertThat((List<?>) response.get("issues")).hasSize(2);
                }

                @Test
                @DisplayName("should include source and target branch names in queued request payload")
                void shouldIncludeSourceAndTargetBranchNamesInQueuedRequestPayload() throws Exception {
                        Map<String, Object> finalEvent = new HashMap<>();
                        finalEvent.put("type", "final");
                        finalEvent.put("result", Map.of("comment", "ok", "issues", List.of()));

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(objectMapper.writeValueAsString(finalEvent));

                        client.performAnalysis(mockRequest);

                        var payloadCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
                        verify(queueService).leftPush(eq("codecrow:analysis:jobs"), payloadCaptor.capture());

                        @SuppressWarnings("unchecked")
                        Map<String, Object> queued = objectMapper.readValue(payloadCaptor.getValue(), Map.class);
                        @SuppressWarnings("unchecked")
                        Map<String, Object> requestPayload = (Map<String, Object>) queued.get("request");

                        assertThat(requestPayload.get("sourceBranchName")).isEqualTo("feature/frontend/GR-2509");
                        assertThat(requestPayload.get("targetBranchName")).isEqualTo("main");
                        assertThat(requestPayload.get("projectWorkspace")).isEqualTo("Codecrow");
                        assertThat(requestPayload.get("projectNamespace")).isEqualTo("codecrow-garden");
                }

                @Test
                @DisplayName("should include enrichmentData with full file content in queued request payload")
                void shouldIncludeEnrichmentDataWithFullFileContentInQueuedRequestPayload() throws Exception {
                        PrEnrichmentDataDto enrichmentData = new PrEnrichmentDataDto(
                                        List.of(FileContentDto.of(
                                                        "magento/app/design/frontend/Perspective/gardeningexpress/Magento_Catalog/templates/product/product-detail-page.phtml",
                                                        "<?php if ($productsToDisplay): ?>\n<div x-data=\"modal()\"></div>\n<?php endif; ?>")),
                                        List.of(ParsedFileMetadataDto.error(
                                                        "magento/app/design/frontend/Perspective/gardeningexpress/Magento_Catalog/templates/product/product-detail-page.phtml",
                                                        "parse_failed")),
                                        List.of(),
                                        new PrEnrichmentDataDto.EnrichmentStats(1, 1, 0, 0, 96, 12, Map.of()));

                        AiAnalysisRequest requestWithEnrichment = AiAnalysisRequestImpl.builder()
                                        .withProjectId(1L)
                                        .withPullRequestId(6L)
                                        .withProjectVcsConnectionBindingInfo("ws", "repo")
                                        .withProjectAiConnectionTokenDecrypted("key")
                                        .withMaxAllowedTokens(1000)
                                        .withUseLocalMcp(false)
                                        .withUseMcpTools(false)
                                        .withProjectMetadata("Codecrow", "codecrow-garden")
                                        .withTargetBranchName("main")
                                        .withSourceBranchName("feature/frontend/GR-2509")
                                        .withChangedFiles(List.of("magento/app/design/frontend/Perspective/gardeningexpress/Magento_Catalog/templates/product/product-detail-page.phtml"))
                                        .withDiffSnippets(List.of("@@ -136,7 +136,7 @@"))
                                        .withRawDiff("diff --git a/... b/...")
                                        .withAnalysisMode(AnalysisMode.FULL)
                                        .withEnrichmentData(enrichmentData)
                                        .build();

                        Map<String, Object> finalEvent = new HashMap<>();
                        finalEvent.put("type", "final");
                        finalEvent.put("result", Map.of("comment", "ok", "issues", List.of()));

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(objectMapper.writeValueAsString(finalEvent));

                        client.performAnalysis(requestWithEnrichment);

                        var payloadCaptor = org.mockito.ArgumentCaptor.forClass(String.class);
                        verify(queueService).leftPush(eq("codecrow:analysis:jobs"), payloadCaptor.capture());

                        @SuppressWarnings("unchecked")
                        Map<String, Object> queued = objectMapper.readValue(payloadCaptor.getValue(), Map.class);
                        @SuppressWarnings("unchecked")
                        Map<String, Object> requestPayload = (Map<String, Object>) queued.get("request");
                        @SuppressWarnings("unchecked")
                        Map<String, Object> serializedEnrichment = (Map<String, Object>) requestPayload.get("enrichmentData");
                        @SuppressWarnings("unchecked")
                        List<Map<String, Object>> fileContents = (List<Map<String, Object>>) serializedEnrichment.get("fileContents");
                        @SuppressWarnings("unchecked")
                        List<Map<String, Object>> fileMetadata = (List<Map<String, Object>>) serializedEnrichment.get("fileMetadata");

                        assertThat(serializedEnrichment).isNotNull();
                        assertThat(fileContents).hasSize(1);
                        assertThat(fileContents.get(0).get("path")).isEqualTo(
                                        "magento/app/design/frontend/Perspective/gardeningexpress/Magento_Catalog/templates/product/product-detail-page.phtml");
                        assertThat(fileContents.get(0).get("content")).isEqualTo(
                                        "<?php if ($productsToDisplay): ?>\n<div x-data=\"modal()\"></div>\n<?php endif; ?>");
                        assertThat(fileMetadata).hasSize(1);
                        assertThat(fileMetadata.get(0).get("path")).isEqualTo(
                                        "magento/app/design/frontend/Perspective/gardeningexpress/Magento_Catalog/templates/product/product-detail-page.phtml");
                }

                @Test
                @DisplayName("should handle intermediate events before final event")
                void shouldHandleIntermediateEvents() throws Exception {
                        Map<String, Object> progressEvent = Map.of("type", "progress", "message", "Working");

                        Map<String, Object> finalEvent = new HashMap<>();
                        finalEvent.put("type", "result");
                        finalEvent.put("result", Map.of("comment", "All good", "issues", List.of()));

                        String progressEventJson = objectMapper.writeValueAsString(progressEvent);
                        String finalEventJson = objectMapper.writeValueAsString(finalEvent);

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(progressEventJson)
                                        .thenReturn(null) // simulate empty wait
                                        .thenReturn(finalEventJson);

                        List<Map<String, Object>> capturedEvents = new ArrayList<>();
                        Consumer<Map<String, Object>> eventHandler = capturedEvents::add;

                        Map<String, Object> response = client.performAnalysis(mockRequest, eventHandler);

                        verify(queueService, times(3)).rightPop(anyString(), anyLong());

                        assertThat(response.get("comment")).isEqualTo("All good");
                        assertThat(capturedEvents).hasSize(2);
                        assertThat(capturedEvents.get(0).get("type")).isEqualTo("progress");
                        assertThat(capturedEvents.get(1).get("type")).isEqualTo("result");
                }
        }

        @Nested
        @DisplayName("performAnalysis() error paths")
        class PerformAnalysisErrorTests {

                @Test
                @DisplayName("should throw IOException when AI service returns error event")
                void shouldThrowWhenErrorEventReceived() throws Exception {
                        Map<String, Object> errorEvent = Map.of(
                                        "type", "error",
                                        "message", "Failed to parse AST");

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(objectMapper.writeValueAsString(errorEvent));

                        assertThatThrownBy(() -> client.performAnalysis(mockRequest))
                                        .isInstanceOf(IOException.class)
                                        .hasMessageContaining("AI service returned error: Failed to parse AST");
                }

                @Test
                @DisplayName("should throw IOException when result payload has error flag")
                void shouldThrowWhenResultHasErrorFlag() throws Exception {
                        Map<String, Object> errorResult = new HashMap<>();
                        errorResult.put("error", true);
                        errorResult.put("error_message", "Model quota exceeded");
                        errorResult.put("comment", "");
                        errorResult.put("issues", List.of());

                        Map<String, Object> finalEvent = new HashMap<>();
                        finalEvent.put("type", "final");
                        finalEvent.put("result", errorResult);

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(objectMapper.writeValueAsString(finalEvent));

                        assertThatThrownBy(() -> client.performAnalysis(mockRequest))
                                        .isInstanceOf(IOException.class)
                                        .hasMessageContaining("Analysis failed: Model quota exceeded");
                }

                @Test
                @DisplayName("should throw IOException when result is missing comment and issues keys")
                void shouldThrowWhenResultIsMalformed() throws Exception {
                        Map<String, Object> malformedResult = new HashMap<>();
                        malformedResult.put("some_key", "value");

                        Map<String, Object> finalEvent = new HashMap<>();
                        finalEvent.put("type", "final");
                        finalEvent.put("result", malformedResult);

                        when(queueService.rightPop(anyString(), anyLong()))
                                        .thenReturn(objectMapper.writeValueAsString(finalEvent));

                        assertThatThrownBy(() -> client.performAnalysis(mockRequest))
                                        .isInstanceOf(IOException.class)
                                        .hasMessageContaining("Analysis data missing required fields");
                }
        }
}
