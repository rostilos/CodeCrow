package org.rostilos.codecrow.mcp;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

import org.rostilos.codecrow.mcp.generic.VcsMcpClientFactory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

/**
 * HTTP-based MCP server for VCS operations.
 * 
 * This is the recommended deployment mode for production/SaaS environments
 * as it avoids JVM startup overhead and enables connection pooling.
 * 
 * Uses JDK's built-in HttpServer for minimal dependencies.
 * 
 * Endpoints:
 *  - POST /mcp/tool/{toolName} - Execute a tool
 *  - GET /mcp/tools - List available tools
 *  - GET /health - Health check
 *  - GET /metrics - Basic metrics
 * 
 * Environment variables:
 *  - MCP_HTTP_PORT: Server port (default: 8765)
 *  - MCP_CACHE_TTL_SECONDS: Cache TTL (default: 300)
 */
public class McpHttpServer {

    private static final Logger log = LoggerFactory.getLogger(McpHttpServer.class);
    private static final ObjectMapper objectMapper = new ObjectMapper();
    
    // Shared tools instance - thread-safe
    private static final McpTools mcpTools = new McpTools(new VcsMcpClientFactory());
    
    // Available tools list
    private static final List<String> AVAILABLE_TOOLS = Arrays.asList(
            "listRepositories", "getRepository", "getPullRequests", "createPullRequest",
            "getPullRequest", "updatePullRequest", "getPullRequestActivity",
            "approvePullRequest", "unapprovePullRequest", "declinePullRequest",
            "mergePullRequest", "getPullRequestComments", "getPullRequestDiff",
            "getPullRequestCommits", "getRepositoryBranchingModel",
            "getRepositoryBranchingModelSettings", "updateRepositoryBranchingModelSettings",
            "getEffectiveRepositoryBranchingModel", "getProjectBranchingModel",
            "getProjectBranchingModelSettings", "updateProjectBranchingModelSettings",
            "getBranches", "createBranch", "deleteBranch", "getCommit", "getCommits",
            "getFileContent", "getDirectoryContent", "createComment", "replyToComment",
            "updateComment", "deleteComment", "resolveComment", "reopenComment"
    );
    
    // Simple in-memory cache with TTL
    private static final ConcurrentHashMap<String, CacheEntry> cache = new ConcurrentHashMap<>();
    private static final int CACHE_TTL_SECONDS = Integer.parseInt(
            System.getenv().getOrDefault("MCP_CACHE_TTL_SECONDS", "300"));
    
    // Metrics
    private static final Metrics metrics = new Metrics();
    
    // Server start time for uptime calculation
    private static final long startTime = System.currentTimeMillis();
    
    // Cache cleanup scheduler
    private static final ScheduledExecutorService cacheCleanup = Executors.newSingleThreadScheduledExecutor();

    public static void main(String[] args) {
        try {
            int port = Integer.parseInt(System.getenv().getOrDefault("MCP_HTTP_PORT", "8765"));
            int threadPoolSize = Integer.parseInt(System.getenv().getOrDefault("MCP_THREAD_POOL", "50"));

            // Start cache cleanup task
            cacheCleanup.scheduleAtFixedRate(
                    McpHttpServer::cleanupExpiredCache,
                    CACHE_TTL_SECONDS,
                    CACHE_TTL_SECONDS,
                    TimeUnit.SECONDS
            );

            HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
            
            // Tool execution endpoint
            server.createContext("/mcp/tool/", new ToolHandler());
            
            // List tools endpoint
            server.createContext("/mcp/tools", new ListToolsHandler());
            
            // Health check endpoint
            server.createContext("/health", new HealthHandler());
            
            // Metrics endpoint
            server.createContext("/metrics", new MetricsHandler());

            // Use thread pool executor
            server.setExecutor(Executors.newFixedThreadPool(threadPoolSize));

            // Add shutdown hook
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                log.info("Shutting down MCP HTTP server...");
                cacheCleanup.shutdown();
                server.stop(5);
            }));

            server.start();
            log.info("VCS MCP HTTP server running on port {} with {} threads", port, threadPoolSize);
            
        } catch (Exception e) {
            log.error("HTTP Server initialization error", e);
            System.exit(1);
        }
    }

    /**
     * Tool execution handler.
     */
    static class ToolHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendError(exchange, 405, "METHOD_NOT_ALLOWED", "Only POST is allowed");
                return;
            }
            
            long requestStart = System.currentTimeMillis();
            String path = exchange.getRequestURI().getPath();
            String toolName = path.substring("/mcp/tool/".length());
            
            if (toolName.isEmpty()) {
                sendError(exchange, 400, "MISSING_TOOL_NAME", "Tool name missing in path");
                return;
            }
            
            metrics.incrementToolCall(toolName);
            
            try {
                // Read request body
                String requestBody;
                try (BufferedReader reader = new BufferedReader(
                        new InputStreamReader(exchange.getRequestBody(), StandardCharsets.UTF_8))) {
                    requestBody = reader.lines().collect(Collectors.joining("\n"));
                }
                
                Map<String, Object> arguments = objectMapper.readValue(
                        requestBody, 
                        new TypeReference<Map<String, Object>>() {}
                );
                
                log.debug("HTTP tool call: {} with args {}", toolName, arguments);

                // Check cache for read-only operations
                String cacheKey = null;
                if (isCacheableOperation(toolName)) {
                    cacheKey = buildCacheKey(toolName, arguments);
                    CacheEntry cached = cache.get(cacheKey);
                    if (cached != null && !cached.isExpired()) {
                        metrics.incrementCacheHit();
                        sendSuccess(exchange, cached.value);
                        return;
                    }
                    metrics.incrementCacheMiss();
                }

                // Execute tool
                Object result = mcpTools.execute(toolName, arguments);
                
                // Cache result if cacheable
                if (cacheKey != null && result != null) {
                    cache.put(cacheKey, new CacheEntry(result, CACHE_TTL_SECONDS));
                }
                
                long duration = System.currentTimeMillis() - requestStart;
                metrics.recordLatency(toolName, duration);
                
                sendSuccess(exchange, result);
                
            } catch (IllegalArgumentException iae) {
                log.warn("Bad request for tool {}: {}", toolName, iae.getMessage());
                metrics.incrementError(toolName);
                sendError(exchange, 400, "INVALID_ARGUMENTS", iae.getMessage());
            } catch (Exception e) {
                log.error("Tool execution error for " + toolName, e);
                metrics.incrementError(toolName);
                sendError(exchange, 500, "EXECUTION_ERROR", "Error executing tool: " + e.getMessage());
            }
        }
    }

    /**
     * List available tools handler.
     */
    static class ListToolsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            if (!"GET".equals(exchange.getRequestMethod())) {
                sendError(exchange, 405, "METHOD_NOT_ALLOWED", "Only GET is allowed");
                return;
            }
            sendSuccess(exchange, Map.of(
                    "tools", AVAILABLE_TOOLS,
                    "count", AVAILABLE_TOOLS.size()
            ));
        }
    }

    /**
     * Health check handler.
     */
    static class HealthHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            Map<String, Object> health = new HashMap<>();
            health.put("status", "UP");
            health.put("timestamp", System.currentTimeMillis());
            health.put("cacheSize", cache.size());
            health.put("uptimeMs", System.currentTimeMillis() - startTime);
            sendSuccess(exchange, health);
        }
    }

    /**
     * Metrics handler.
     */
    static class MetricsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            sendSuccess(exchange, metrics.getSnapshot());
        }
    }

    // Helper methods
    
    private static void sendSuccess(HttpExchange exchange, Object result) throws IOException {
        byte[] response = objectMapper.writeValueAsBytes(result);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(200, response.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(response);
        }
    }
    
    private static void sendError(HttpExchange exchange, int status, String code, String message) throws IOException {
        Map<String, Object> error = Map.of(
                "error", true,
                "code", code,
                "message", message
        );
        byte[] response = objectMapper.writeValueAsBytes(error);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, response.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(response);
        }
    }

    private static boolean isCacheableOperation(String toolName) {
        // Cache read-only operations
        return toolName.startsWith("get") || toolName.startsWith("list");
    }

    private static String buildCacheKey(String toolName, Map<String, Object> arguments) {
        try {
            return toolName + ":" + objectMapper.writeValueAsString(arguments);
        } catch (Exception e) {
            return toolName + ":" + arguments.hashCode();
        }
    }

    private static void cleanupExpiredCache() {
        int removed = 0;
        for (Map.Entry<String, CacheEntry> entry : cache.entrySet()) {
            if (entry.getValue().isExpired()) {
                cache.remove(entry.getKey());
                removed++;
            }
        }
        if (removed > 0) {
            log.debug("Cleaned up {} expired cache entries", removed);
        }
    }

    /**
     * Cache entry with TTL.
     */
    private static class CacheEntry {
        final Object value;
        final long expiresAt;

        CacheEntry(Object value, int ttlSeconds) {
            this.value = value;
            this.expiresAt = System.currentTimeMillis() + (ttlSeconds * 1000L);
        }

        boolean isExpired() {
            return System.currentTimeMillis() > expiresAt;
        }
    }

    /**
     * Simple metrics collector.
     */
    private static class Metrics {
        private final ConcurrentHashMap<String, Long> toolCalls = new ConcurrentHashMap<>();
        private final ConcurrentHashMap<String, Long> toolErrors = new ConcurrentHashMap<>();
        private final ConcurrentHashMap<String, Long> toolLatencySum = new ConcurrentHashMap<>();
        private long cacheHits = 0;
        private long cacheMisses = 0;

        void incrementToolCall(String tool) {
            toolCalls.merge(tool, 1L, Long::sum);
        }

        void incrementError(String tool) {
            toolErrors.merge(tool, 1L, Long::sum);
        }

        void recordLatency(String tool, long latencyMs) {
            toolLatencySum.merge(tool, latencyMs, Long::sum);
        }

        synchronized void incrementCacheHit() {
            cacheHits++;
        }

        synchronized void incrementCacheMiss() {
            cacheMisses++;
        }

        Map<String, Object> getSnapshot() {
            Map<String, Object> snapshot = new HashMap<>();
            snapshot.put("toolCalls", new HashMap<>(toolCalls));
            snapshot.put("toolErrors", new HashMap<>(toolErrors));
            snapshot.put("cacheHits", cacheHits);
            snapshot.put("cacheMisses", cacheMisses);
            snapshot.put("cacheHitRate", cacheHits + cacheMisses > 0 
                    ? (double) cacheHits / (cacheHits + cacheMisses) : 0.0);
            
            // Calculate average latencies
            Map<String, Double> avgLatencies = new HashMap<>();
            for (String tool : toolCalls.keySet()) {
                long calls = toolCalls.getOrDefault(tool, 1L);
                long totalLatency = toolLatencySum.getOrDefault(tool, 0L);
                avgLatencies.put(tool, (double) totalLatency / calls);
            }
            snapshot.put("avgLatencyMs", avgLatencies);
            
            return snapshot;
        }
    }
}
