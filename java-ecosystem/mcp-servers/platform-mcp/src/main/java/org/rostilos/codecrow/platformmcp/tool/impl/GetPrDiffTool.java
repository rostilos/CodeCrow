package org.rostilos.codecrow.platformmcp.tool.impl;

import java.util.HashMap;
import java.util.Map;

import org.rostilos.codecrow.platformmcp.tool.PlatformTool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Tool to get the diff/changes of a pull request.
 */
public class GetPrDiffTool implements PlatformTool {
    
    private static final Logger log = LoggerFactory.getLogger(GetPrDiffTool.class);
    
    @Override
    public String getName() {
        return "getPrDiff";
    }
    
    @Override
    public String getDescription() {
        return "Get the code diff/changes from a pull request";
    }
    
    @Override
    public Object execute(Map<String, Object> arguments) throws Exception {
        Long projectId = getLongArg(arguments, "projectId");
        Integer prId = getIntArg(arguments, "prId");
        String filePath = getStringArg(arguments, "filePath");
        Boolean contextOnly = getBoolArg(arguments, "contextOnly");
        
        if (projectId == null) {
            throw new IllegalArgumentException("projectId is required");
        }
        if (prId == null) {
            throw new IllegalArgumentException("prId is required");
        }
        
        log.info("Getting PR diff for projectId={}, prId={}, filePath={}", 
                projectId, prId, filePath);
        
        // TODO: Implement actual VCS client integration
        Map<String, Object> result = new HashMap<>();
        result.put("projectId", projectId);
        result.put("prId", prId);
        result.put("status", "PENDING_IMPLEMENTATION");
        result.put("message", "PR diff retrieval is pending VCS client integration.");
        
        if (filePath != null) {
            result.put("requestedFile", filePath);
        }
        if (contextOnly != null && contextOnly) {
            result.put("contextOnly", true);
            result.put("contextDescription", "When contextOnly=true, returns only changed lines with minimal surrounding context");
        }
        
        result.put("expectedFields", Map.of(
            "diffContent", "String - Raw diff in unified format",
            "files", "Array - List of changed files with their diffs",
            "additions", "Integer - Total lines added",
            "deletions", "Integer - Total lines deleted",
            "changedFiles", "Integer - Number of files changed"
        ));
        
        return result;
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
    
    private Boolean getBoolArg(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) return null;
        if (value instanceof Boolean) return (Boolean) value;
        if (value instanceof String) return Boolean.parseBoolean((String) value);
        return null;
    }
}
