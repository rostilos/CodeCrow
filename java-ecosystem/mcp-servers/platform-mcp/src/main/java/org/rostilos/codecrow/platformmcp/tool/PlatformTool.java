package org.rostilos.codecrow.platformmcp.tool;

import java.util.Map;

/**
 * Interface for Platform MCP tools.
 */
public interface PlatformTool {
    
    /**
     * Get the tool name (must match the tool definition in PlatformMcpServer).
     */
    String getName();
    
    /**
     * Get the tool description.
     */
    String getDescription();
    
    /**
     * Execute the tool with the given arguments.
     * 
     * @param arguments The tool arguments as a map
     * @return The result object (will be serialized to JSON)
     * @throws Exception If execution fails
     */
    Object execute(Map<String, Object> arguments) throws Exception;
}
