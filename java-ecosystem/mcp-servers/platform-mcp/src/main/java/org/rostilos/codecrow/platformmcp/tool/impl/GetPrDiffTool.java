package org.rostilos.codecrow.platformmcp.tool.impl;

import java.util.HashMap;
import java.util.Map;

import org.rostilos.codecrow.platformmcp.service.VcsService;
import org.rostilos.codecrow.platformmcp.tool.PlatformTool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Tool to get the diff/changes of a pull request.
 * Uses VcsService which reuses vcs-client library for VCS API access.
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
        
        VcsService vcsService = VcsService.getInstance();
        
        if (!vcsService.isInitialized()) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", "VCS service not initialized - missing credentials");
            error.put("projectId", projectId);
            error.put("prId", prId);
            error.put("hint", "Ensure workspace, repo.slug, and authentication (accessToken or oAuthClient/oAuthSecret) are provided");
            return error;
        }
        
        try {
            return vcsService.getPullRequestDiff(projectId, prId, filePath, contextOnly);
        } catch (Exception e) {
            log.error("Failed to get PR diff: {}", e.getMessage(), e);
            Map<String, Object> error = new HashMap<>();
            error.put("error", e.getMessage());
            error.put("projectId", projectId);
            error.put("prId", prId);
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
    
    private Boolean getBoolArg(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) return null;
        if (value instanceof Boolean) return (Boolean) value;
        if (value instanceof String) return Boolean.parseBoolean((String) value);
        return null;
    }
}
