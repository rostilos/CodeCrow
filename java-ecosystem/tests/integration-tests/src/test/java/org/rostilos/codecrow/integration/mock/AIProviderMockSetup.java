package org.rostilos.codecrow.integration.mock;

import com.github.tomakehurst.wiremock.WireMockServer;

import static com.github.tomakehurst.wiremock.client.WireMock.*;

/**
 * Helper class for setting up WireMock stubs for AI provider APIs.
 */
public class AIProviderMockSetup {

    private final WireMockServer openaiServer;
    private final WireMockServer anthropicServer;
    private final WireMockServer openrouterServer;

    public AIProviderMockSetup(WireMockServer openaiServer, WireMockServer anthropicServer, WireMockServer openrouterServer) {
        this.openaiServer = openaiServer;
        this.anthropicServer = anthropicServer;
        this.openrouterServer = openrouterServer;
    }

    /**
     * Simplified constructor that uses the same server for all AI providers.
     * Useful for tests that only need OpenRouter (the default AI provider).
     */
    public AIProviderMockSetup(WireMockServer openrouterServer) {
        this.openaiServer = openrouterServer;
        this.anthropicServer = openrouterServer;
        this.openrouterServer = openrouterServer;
    }

    public void setupOpenAIChatCompletion(String model, String response) {
        openaiServer.stubFor(post(urlPathEqualTo("/v1/chat/completions"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "id": "chatcmpl-test-id",
                                "object": "chat.completion",
                                "created": 1704067200,
                                "model": "%s",
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": {
                                            "role": "assistant",
                                            "content": "%s"
                                        },
                                        "finish_reason": "stop"
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 100,
                                    "completion_tokens": 50,
                                    "total_tokens": 150
                                }
                            }
                            """, model, escapeJson(response)))));
    }

    public void setupOpenAIStreamingChatCompletion(String model, String response) {
        openaiServer.stubFor(post(urlPathEqualTo("/v1/chat/completions"))
                .withHeader("Accept", containing("text/event-stream"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "text/event-stream")
                        .withBody(String.format("""
                            data: {"id":"chatcmpl-test","object":"chat.completion.chunk","model":"%s","choices":[{"index":0,"delta":{"content":"%s"},"finish_reason":null}]}
                            
                            data: {"id":"chatcmpl-test","object":"chat.completion.chunk","model":"%s","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
                            
                            data: [DONE]
                            """, model, escapeJson(response), model))));
    }

    public void setupAnthropicMessages(String model, String response) {
        anthropicServer.stubFor(post(urlPathEqualTo("/v1/messages"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "id": "msg_test_id",
                                "type": "message",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "%s"
                                    }
                                ],
                                "model": "%s",
                                "stop_reason": "end_turn",
                                "usage": {
                                    "input_tokens": 100,
                                    "output_tokens": 50
                                }
                            }
                            """, escapeJson(response), model))));
    }

    public void setupOpenRouterChatCompletion(String model, String response) {
        openrouterServer.stubFor(post(urlPathEqualTo("/api/v1/chat/completions"))
                .willReturn(aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody(String.format("""
                            {
                                "id": "gen-test-id",
                                "model": "%s",
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": {
                                            "role": "assistant",
                                            "content": "%s"
                                        },
                                        "finish_reason": "stop"
                                    }
                                ],
                                "usage": {
                                    "prompt_tokens": 100,
                                    "completion_tokens": 50,
                                    "total_tokens": 150
                                }
                            }
                            """, model, escapeJson(response)))));
    }

    public void setupOpenAIInvalidApiKey() {
        openaiServer.stubFor(post(urlPathMatching("/v1/.*"))
                .willReturn(aResponse()
                        .withStatus(401)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "error": {
                                    "message": "Incorrect API key provided",
                                    "type": "invalid_request_error",
                                    "code": "invalid_api_key"
                                }
                            }
                            """)));
    }

    public void setupAnthropicInvalidApiKey() {
        anthropicServer.stubFor(post(urlPathMatching("/v1/.*"))
                .willReturn(aResponse()
                        .withStatus(401)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "type": "error",
                                "error": {
                                    "type": "authentication_error",
                                    "message": "Invalid API key"
                                }
                            }
                            """)));
    }

    public void setupOpenRouterInvalidApiKey() {
        openrouterServer.stubFor(post(urlPathMatching("/api/v1/.*"))
                .willReturn(aResponse()
                        .withStatus(401)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""
                            {
                                "error": {
                                    "message": "Invalid API key",
                                    "code": 401
                                }
                            }
                            """)));
    }

    public void setupCodeAnalysisResponse(String analysisResult) {
        String escapedResult = escapeJson(analysisResult);
        
        setupOpenAIChatCompletion("gpt-4o-mini", escapedResult);
        setupAnthropicMessages("claude-3-haiku-20240307", escapedResult);
        setupOpenRouterChatCompletion("anthropic/claude-3-haiku", escapedResult);
    }

    /**
     * Sets up a generic chat completion response across all AI providers.
     * Use this when you just need a simple response for testing.
     */
    public void setupChatCompletion(String response) {
        setupOpenAIChatCompletion("gpt-4o-mini", response);
        setupAnthropicMessages("claude-3-haiku-20240307", response);
        setupOpenRouterChatCompletion("anthropic/claude-3-haiku", response);
    }

    public void setupPullRequestReviewResponse() {
        String reviewResponse = """
            ## Code Review Summary
            
            ### Issues Found
            1. **Security Issue**: Potential SQL injection in line 42
            2. **Performance**: Inefficient loop detected in line 78
            
            ### Suggestions
            - Use parameterized queries
            - Consider using stream API for better performance
            
            ### Overall Assessment
            The code needs some improvements before merging.
            """;
        
        setupCodeAnalysisResponse(reviewResponse);
    }

    private String escapeJson(String text) {
        return text
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }
}
