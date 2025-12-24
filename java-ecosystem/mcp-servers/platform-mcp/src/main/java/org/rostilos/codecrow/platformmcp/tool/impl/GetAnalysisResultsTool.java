package org.rostilos.codecrow.platformmcp.tool.impl;

import java.util.HashMap;
import java.util.Map;

import org.rostilos.codecrow.platformmcp.service.PlatformApiService;
import org.rostilos.codecrow.platformmcp.tool.PlatformTool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Tool to get analysis results for a pull request.
 * 
 * Arguments:
 * - projectId (required): The project ID
 * - prNumber (required): The pull request number
 * - commitHash (optional): Specific commit hash
 */
public class GetAnalysisResultsTool implements PlatformTool {
    
    private static final Logger log = LoggerFactory.getLogger(GetAnalysisResultsTool.class);
    
    @Override
    public String getName() {
        return "getAnalysisResults";
    }
    
    @Override
    public String getDescription() {
        return "Get code analysis results for a pull request";
    }
    
    @Override
    public Object execute(Map<String, Object> arguments) throws Exception {
        Long projectId = getLongArg(arguments, "projectId");
        Long prNumber = getLongArg(arguments, "prNumber");
        String commitHash = (String) arguments.get("commitHash");
        
        if (projectId == null) {
            throw new IllegalArgumentException("projectId is required");
        }
        if (prNumber == null) {
            throw new IllegalArgumentException("prNumber is required");
        }
        
        log.info("Getting analysis results for project={}, PR={}, commit={}", 
            projectId, prNumber, commitHash);

        PlatformApiService apiService = PlatformApiService.getInstance();
        Map<String, Object> analysisResults = apiService.getAnalysisResults(prNumber);
        if (analysisResults == null) {
            Map<String, Object> notFound = new HashMap<>();
            notFound.put("error", "Analysis Results not found or not yet available");
            notFound.put("prNumber", prNumber);
            return notFound;
        }
        return analysisResults;
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
