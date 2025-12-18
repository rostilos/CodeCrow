package org.rostilos.codecrow.platformmcp.tool;

import java.util.HashMap;
import java.util.Map;

import org.rostilos.codecrow.platformmcp.tool.impl.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Registry and executor for Platform MCP tools.
 */
public class PlatformMcpTools {
    
    private static final Logger log = LoggerFactory.getLogger(PlatformMcpTools.class);
    
    private final Map<String, PlatformTool> tools = new HashMap<>();
    
    public PlatformMcpTools() {
        registerTools();
    }
    
    private void registerTools() {
        // Register all available tools
        register(new GetAnalysisResultsTool());
        register(new GetIssueDetailsTool());
        register(new ListProjectAnalysesTool());
        register(new SearchIssuesTool());
        register(new GetPrDataTool());
        register(new GetPrDiffTool());
        register(new AskAboutAnalysisTool());
    }
    
    private void register(PlatformTool tool) {
        tools.put(tool.getName(), tool);
        log.debug("Registered tool: {}", tool.getName());
    }
    
    /**
     * Execute a tool by name with the given arguments.
     */
    public Object execute(String toolName, Map<String, Object> arguments) throws Exception {
        PlatformTool tool = tools.get(toolName);
        if (tool == null) {
            throw new IllegalArgumentException("Unknown tool: " + toolName);
        }
        
        log.info("Executing tool: {} with {} arguments", toolName, arguments != null ? arguments.size() : 0);
        
        try {
            return tool.execute(arguments);
        } catch (Exception e) {
            log.error("Error executing tool {}: {}", toolName, e.getMessage(), e);
            throw e;
        }
    }
}
