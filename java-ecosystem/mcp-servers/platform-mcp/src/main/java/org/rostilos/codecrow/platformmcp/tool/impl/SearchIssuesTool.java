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
    private final PlatformApiService apiService;

    public SearchIssuesTool() {
        this(null);
    }

    SearchIssuesTool(PlatformApiService apiService) {
        this.apiService = apiService;
    }

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
        Integer requestedLimit = getIntArg(arguments, "limit");
        int limit = requestedLimit == null || requestedLimit <= 0
                ? DEFAULT_LIMIT
                : Math.min(requestedLimit, MAX_LIMIT);

        PlatformApiService activeApiService = apiService != null
                ? apiService
                : PlatformApiService.getInstance();
        Long projectId = activeApiService.getProjectId();
        
        log.info("Searching issues for projectId={}, severity={}, category={}, status={}", 
                projectId, severity, category, status);
        
        try {
            List<Map<String, Object>> completeIssues = activeApiService.searchIssues(
                    severity, category, status);
            int admittedCount = Math.min(limit, completeIssues.size());
            List<Map<String, Object>> issues = List.copyOf(
                    completeIssues.subList(0, admittedCount));
            int omittedCount = completeIssues.size() - admittedCount;
            
            Map<String, Object> result = new HashMap<>();
            result.put("projectId", projectId);
            result.put("issues", issues);
            result.put("count", issues.size());
            result.put("totalCount", completeIssues.size());
            result.put("issuesComplete", omittedCount == 0);
            result.put("omittedIssueCount", omittedCount);
            result.put("issuesStatus", omittedCount == 0
                    ? "complete"
                    : "bounded_for_mcp_prompt");
            if (omittedCount > 0) {
                result.put("issuesDiagnostic",
                        "Issue projection reached the MCP prompt limit; omitted issues "
                                + "remain available through the complete internal API and "
                                + "must not be treated as absent.");
            }
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
        if (value instanceof Number number) return number.intValue();
        if (value instanceof String stringValue) {
            try {
                return Integer.parseInt(stringValue);
            } catch (NumberFormatException ignored) {
                return null;
            }
        }
        return null;
    }
    
    private String getStringArg(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) return null;
        return value.toString();
    }
}
