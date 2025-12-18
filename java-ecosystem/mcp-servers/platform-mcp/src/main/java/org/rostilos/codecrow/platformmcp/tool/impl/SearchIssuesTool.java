package org.rostilos.codecrow.platformmcp.tool.impl;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.rostilos.codecrow.platformmcp.service.PlatformApiService;
import org.rostilos.codecrow.platformmcp.tool.PlatformTool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Tool to search issues across analyses with various filters via API.
 * Security: Uses project.id from JVM properties (from validated webhook chain).
 */
public class SearchIssuesTool implements PlatformTool {
    
    private static final Logger log = LoggerFactory.getLogger(SearchIssuesTool.class);
    private static final int DEFAULT_LIMIT = 50;
    private static final int MAX_LIMIT = 200;
    
    @Override
    public String getName() {
        return "searchIssues";
    }
    
    @Override
    public String getDescription() {
        return "Search for code issues in the current project with optional filters by severity, category, or status";
    }
    
    @Override
    public Object execute(Map<String, Object> arguments) throws Exception {
        String severity = getStringArg(arguments, "severity");
        String category = getStringArg(arguments, "category");
        String status = getStringArg(arguments, "status");
        Integer limit = getIntArg(arguments, "limit");
        
        if (limit == null || limit <= 0) {
            limit = DEFAULT_LIMIT;
        }
        if (limit > MAX_LIMIT) {
            limit = MAX_LIMIT;
        }
        
        PlatformApiService apiService = PlatformApiService.getInstance();
        Long projectId = apiService.getProjectId();
        
        log.info("Searching issues for projectId={}, severity={}, category={}, status={}", 
                projectId, severity, category, status);
        
        try {
            List<Map<String, Object>> issues = apiService.searchIssues(severity, category, status, limit);
            
            Map<String, Object> result = new HashMap<>();
            result.put("projectId", projectId);
            result.put("issues", issues);
            result.put("count", issues.size());
            result.put("filters", Map.of(
                "severity", severity != null ? severity : "all",
                "category", category != null ? category : "all",
                "status", status != null ? status : "all"
            ));
            
            return result;
        } catch (Exception e) {
            log.error("Error searching issues: {}", e.getMessage(), e);
            Map<String, Object> error = new HashMap<>();
            error.put("error", e.getMessage());
            return error;
        }
    }
    
    private Integer getIntArg(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) return null;
        if (value instanceof Integer) return (Integer) value;
        if (value instanceof Long) return ((Long) value).intValue();
        if (value instanceof String) return Integer.parseInt((String) value);
        return null;
    }
    
    private String getStringArg(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) return null;
        return value.toString();
    }
}