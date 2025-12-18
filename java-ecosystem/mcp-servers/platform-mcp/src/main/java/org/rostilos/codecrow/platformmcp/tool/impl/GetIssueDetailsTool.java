package org.rostilos.codecrow.platformmcp.tool.impl;

import java.util.HashMap;
import java.util.Map;

import org.rostilos.codecrow.platformmcp.service.PlatformApiService;
import org.rostilos.codecrow.platformmcp.tool.PlatformTool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Tool to get detailed information about a specific issue via API.
 */
public class GetIssueDetailsTool implements PlatformTool {
    
    private static final Logger log = LoggerFactory.getLogger(GetIssueDetailsTool.class);
    
    @Override
    public String getName() {
        return "getIssueDetails";
    }
    
    @Override
    public String getDescription() {
        return "Get detailed information about a specific code issue from the analysis. Use when user asks about a specific issue number.";
    }
    
    @Override
    public Object execute(Map<String, Object> arguments) throws Exception {
        Long issueId = getLongArg(arguments, "issueId");
        
        if (issueId == null) {
            throw new IllegalArgumentException("issueId is required");
        }
        
        log.info("Getting issue details for issueId={}", issueId);
        
        try {
            PlatformApiService apiService = PlatformApiService.getInstance();
            Map<String, Object> issueDetails = apiService.getIssueDetails(issueId);
            
            if (issueDetails == null) {
                Map<String, Object> notFound = new HashMap<>();
                notFound.put("error", "Issue not found");
                notFound.put("issueId", issueId);
                return notFound;
            }
            
            return issueDetails;
        } catch (Exception e) {
            log.error("Error fetching issue details: {}", e.getMessage(), e);
            Map<String, Object> error = new HashMap<>();
            error.put("error", e.getMessage());
            error.put("issueId", issueId);
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
}
