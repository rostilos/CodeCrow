package org.rostilos.codecrow.platformmcp;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import org.rostilos.codecrow.platformmcp.tool.PlatformMcpTools;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

import io.modelcontextprotocol.server.McpServer;
import io.modelcontextprotocol.server.McpServerFeatures;
import io.modelcontextprotocol.server.McpSyncServer;
import io.modelcontextprotocol.server.transport.StdioServerTransportProvider;
import io.modelcontextprotocol.spec.McpSchema;
import io.modelcontextprotocol.spec.McpSchema.CallToolResult;
import io.modelcontextprotocol.spec.McpSchema.TextContent;
import io.modelcontextprotocol.spec.McpSchema.Tool;

/**
 * MCP Server for CodeCrow Platform.
 * 
 * Provides tools for accessing:
 * - Analysis results and issues
 * - Project configuration
 * - PR summaries and cached data
 * - Issue search and filtering
 * 
 * This server is designed to be used by AI assistants to answer questions
 * about code analysis results and project data.
 */
public class PlatformMcpServer {

    private static final Logger log = LoggerFactory.getLogger(PlatformMcpServer.class);
    private static final ObjectMapper objectMapper;
    private static final PlatformMcpTools mcpTools;
    
    static {
        objectMapper = new ObjectMapper();
        objectMapper.registerModule(new JavaTimeModule());
        mcpTools = new PlatformMcpTools();
    }

    public static void main(String[] args) {
        try {
            log.info("Starting CodeCrow Platform MCP Server...");
            
            var transportProvider = new StdioServerTransportProvider(objectMapper);

            McpSyncServer syncServer = McpServer.sync(transportProvider)
                    .serverInfo("codecrow-platform-mcp", "1.0.0")
                    .capabilities(McpSchema.ServerCapabilities.builder()
                            .tools(true)
                            .resources(true, true)
                            .logging()
                            .build())
                    .tools(getToolSpecifications())
                    .build();

            log.info("CodeCrow Platform MCP server running on stdio...");
        } catch (Exception e) {
            log.error("Server initialization error", e);
            System.exit(1);
        }
    }

    private static List<McpServerFeatures.SyncToolSpecification> getToolSpecifications() {
        List<Tool> tools = defineTools();
        List<McpServerFeatures.SyncToolSpecification> specifications = new ArrayList<>();

        for (Tool tool : tools) {
            McpServerFeatures.SyncToolSpecification spec = new McpServerFeatures.SyncToolSpecification(
                    tool,
                    (exchange, arguments) -> {
                        try {
                            log.debug("Executing tool: {} with args: {}", tool.name(), arguments);
                            Object result = mcpTools.execute(tool.name(), arguments);
                            String jsonResult = objectMapper.writeValueAsString(result);
                            return new CallToolResult(List.of(new TextContent(jsonResult)), false);
                        } catch (Exception e) {
                            log.error("Tool execution error for " + tool.name(), e);
                            return new CallToolResult(
                                List.of(new TextContent("Error executing tool: " + e.getMessage())), 
                                true
                            );
                        }
                    }
            );
            specifications.add(spec);
        }

        return specifications;
    }

    private static List<Tool> defineTools() {
        List<Tool> tools = new ArrayList<>();

        // getAnalysisResults - Get PR analysis results
        String getAnalysisResultsSchema = """
                {
                  "type": "object",
                  "properties": {
                    "projectId": {
                      "type": "integer",
                      "description": "The project ID"
                    },
                    "prNumber": {
                      "type": "integer",
                      "description": "The pull request number"
                    },
                    "commitHash": {
                      "type": "string",
                      "description": "Optional commit hash to get specific analysis"
                    }
                  },
                  "required": ["projectId", "prNumber"]
                }
                """;
        tools.add(new Tool(
            "getAnalysisResults", 
            "Get code analysis results for a pull request. Returns issues found, severity counts, and analysis metadata.",
            getAnalysisResultsSchema
        ));

        // getIssueDetails - Get specific issue details
        String getIssueDetailsSchema = """
                {
                  "type": "object",
                  "properties": {
                    "issueId": {
                      "type": "integer",
                      "description": "The issue ID"
                    }
                  },
                  "required": ["issueId"]
                }
                """;
        tools.add(new Tool(
            "getIssueDetails", 
            "Get detailed information about a specific code issue including file path, line number, description, and suggested fix.",
            getIssueDetailsSchema
        ));

        // listProjectAnalyses - List recent analyses
        String listProjectAnalysesSchema = """
                {
                  "type": "object",
                  "properties": {
                    "projectId": {
                      "type": "integer",
                      "description": "The project ID"
                    },
                    "limit": {
                      "type": "integer",
                      "description": "Maximum number of analyses to return (default: 10)"
                    },
                    "prNumber": {
                      "type": "integer",
                      "description": "Optional: filter by PR number"
                    }
                  },
                  "required": ["projectId"]
                }
                """;
        tools.add(new Tool(
            "listProjectAnalyses", 
            "List recent code analyses for a project with summary statistics.",
            listProjectAnalysesSchema
        ));

        // searchIssues - Search issues across project
        String searchIssuesSchema = """
                {
                  "type": "object",
                  "properties": {
                    "projectId": {
                      "type": "integer",
                      "description": "The project ID"
                    },
                    "query": {
                      "type": "string",
                      "description": "Search query to match against issue descriptions"
                    },
                    "severity": {
                      "type": "string",
                      "enum": ["HIGH", "MEDIUM", "LOW", "INFO"],
                      "description": "Filter by severity level"
                    },
                    "category": {
                      "type": "string",
                      "description": "Filter by issue category (e.g., security, performance, style)"
                    },
                    "filePath": {
                      "type": "string",
                      "description": "Filter by file path pattern"
                    },
                    "limit": {
                      "type": "integer",
                      "description": "Maximum number of issues to return (default: 20)"
                    }
                  },
                  "required": ["projectId"]
                }
                """;
        tools.add(new Tool(
            "searchIssues", 
            "Search for code issues in a project with optional filters for severity, category, and file path.",
            searchIssuesSchema
        ));

        // getPrSummary - Get cached PR summary
        String getPrSummarySchema = """
                {
                  "type": "object",
                  "properties": {
                    "projectId": {
                      "type": "integer",
                      "description": "The project ID"
                    },
                    "prNumber": {
                      "type": "integer",
                      "description": "The pull request number"
                    },
                    "commitHash": {
                      "type": "string",
                      "description": "Optional commit hash for specific summary version"
                    }
                  },
                  "required": ["projectId", "prNumber"]
                }
                """;
        tools.add(new Tool(
            "getPrSummary", 
            "Get the cached summary for a pull request including description, key changes, and diagrams.",
            getPrSummarySchema
        ));

        // getProjectConfig - Get project configuration
        String getProjectConfigSchema = """
                {
                  "type": "object",
                  "properties": {
                    "projectId": {
                      "type": "integer",
                      "description": "The project ID"
                    }
                  },
                  "required": ["projectId"]
                }
                """;
        tools.add(new Tool(
            "getProjectConfig", 
            "Get project configuration including analysis settings, branch patterns, and RAG configuration.",
            getProjectConfigSchema
        ));

        // getIssueSeveritySummary - Get severity breakdown
        String getIssueSeveritySummarySchema = """
                {
                  "type": "object",
                  "properties": {
                    "projectId": {
                      "type": "integer",
                      "description": "The project ID"
                    },
                    "analysisId": {
                      "type": "integer",
                      "description": "Optional: specific analysis ID"
                    }
                  },
                  "required": ["projectId"]
                }
                """;
        tools.add(new Tool(
            "getIssueSeveritySummary", 
            "Get a summary of issues grouped by severity level for a project or specific analysis.",
            getIssueSeveritySummarySchema
        ));

        return tools;
    }
}
