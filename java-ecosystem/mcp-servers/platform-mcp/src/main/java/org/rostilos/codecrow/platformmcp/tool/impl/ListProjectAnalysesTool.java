package org.rostilos.codecrow.platformmcp.tool.impl;

import java.util.HashMap;
import java.util.Map;

import org.rostilos.codecrow.platformmcp.service.PlatformApiService;
import org.rostilos.codecrow.platformmcp.tool.PlatformTool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Tool to list recent analyses for a project.
 */
public class ListProjectAnalysesTool implements PlatformTool {
    
    private static final Logger log = LoggerFactory.getLogger(ListProjectAnalysesTool.class);
    private static final int DEFAULT_LIMIT = 20;
    private static final int MAX_LIMIT = 100;
    
    @Override
    public String getName() {
        return "listProjectAnalyses";
    }
    
    @Override
    public String getDescription() {
        return "List recent code analyses for a project";
    }
    
    @Override
    public Object execute(Map<String, Object> arguments) throws Exception {
        Long projectId = getLongArg(arguments, "projectId");
        Integer limit = getIntArg(arguments, "limit");
        Integer offset = getIntArg(arguments, "offset");
        String status = getStringArg(arguments, "status");
        
        if (projectId == null) {
            throw new IllegalArgumentException("projectId is required");
        }
        
        if (limit == null || limit <= 0) {
            limit = DEFAULT_LIMIT;
        }
        if (limit > MAX_LIMIT) {
            limit = MAX_LIMIT;
        }
        if (offset == null || offset < 0) {
            offset = 0;
        }
        
        log.info("Listing analyses for projectId={}, limit={}, offset={}, status={}", 
                projectId, limit, offset, status);
        
        try {
            PlatformApiService apiService = PlatformApiService.getInstance();
            Map<String, Object> result = apiService.listAnalyses(status, limit, offset);
            
            if (result == null) {
                Map<String, Object> notFound = new HashMap<>();
                notFound.put("error", "No analyses found for the project");
                notFound.put("projectId", projectId);
                return notFound;
            }
            
            return result;
        } catch (Exception e) {
            log.error("Error listing analyses: {}", e.getMessage(), e);
            Map<String, Object> error = new HashMap<>();
            error.put("error", e.getMessage());
            error.put("projectId", projectId);
            return error;
        }
    }
    
    private Long getLongArg(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) return null;
        if (value instanceof Long) return (Long) value;
        if (value instanceof Integer) return ((Integer) value).longValue();
        if (value instanceof String) return Long.parseLong((String) value);
        return null;
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
