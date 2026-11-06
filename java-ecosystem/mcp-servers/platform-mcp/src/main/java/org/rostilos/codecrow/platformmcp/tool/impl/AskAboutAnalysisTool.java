package org.rostilos.codecrow.platformmcp.tool.impl;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.rostilos.codecrow.platformmcp.service.PlatformApiService;
import org.rostilos.codecrow.platformmcp.tool.PlatformTool;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Tool to ask AI-powered questions about an analysis.
 * 
 * Note: This tool provides analysis context for AI assistants to answer questions.
 * The actual AI response generation happens in the Inference Orchestrator layer which 
 * combines this data with RAG context and LLM capabilities.
 * 
 * This tool fetches:
 * - Analysis summary and statistics
 * - Issues related to the question topic
 * - Relevant code context
 */
public class AskAboutAnalysisTool implements PlatformTool {
    
    private static final Logger log = LoggerFactory.getLogger(AskAboutAnalysisTool.class);
    private static final int MAX_QUESTION_LENGTH = 2000;
    private static final int MAX_ANALYSIS_ISSUES = 50;
    private static final int MAX_RELATED_ISSUES = 10;
    private final PlatformApiService apiService;

    public AskAboutAnalysisTool() {
        this(null);
    }

    AskAboutAnalysisTool(PlatformApiService apiService) {
        this.apiService = apiService;
    }

    @Override
    public String getName() {
        return "askAboutAnalysis";
    }
    
    @Override
    public String getDescription() {
        return "Get analysis context to help answer a question about code analysis results. Returns relevant issues and analysis data.";
    }
    
    @Override
    public Object execute(Map<String, Object> arguments) throws Exception {
        Long analysisId = getLongArg(arguments, "analysisId");
        String question = getStringArg(arguments, "question");
        Boolean includeContext = getBoolArg(arguments, "includeContext");
        
        if (analysisId == null) {
            throw new IllegalArgumentException("analysisId is required");
        }
        if (question == null || question.trim().isEmpty()) {
            throw new IllegalArgumentException("question is required");
        }
        if (question.length() > MAX_QUESTION_LENGTH) {
            throw new IllegalArgumentException("Question exceeds maximum length of " + MAX_QUESTION_LENGTH + " characters");
        }
        
        log.info("Getting analysis context for question about analysisId={}, question length={}", 
                analysisId, question.length());
        
        PlatformApiService activeApiService = apiService != null
                ? apiService
                : PlatformApiService.getInstance();
        
        // Get the analysis data
        Map<String, Object> analysisData;
        try {
            analysisData = activeApiService.getAnalysisById(analysisId);
        } catch (Exception e) {
            log.warn("Failed to fetch analysis {}: {}", analysisId, e.getMessage());
            analysisData = null;
        }
        
        if (analysisData == null) {
            Map<String, Object> notFound = new HashMap<>();
            notFound.put("error", "Analysis not found");
            notFound.put("analysisId", analysisId);
            return notFound;
        }
        
        // Search for issues related to the question keywords
        List<Map<String, Object>> relatedIssues = null;
        Integer relatedIssueTotal = null;
        try {
            // Extract keywords from question for search
            String searchCategory = extractCategoryFromQuestion(question);
            String searchSeverity = extractSeverityFromQuestion(question);
            
            List<Map<String, Object>> completeRelatedIssues =
                    activeApiService.searchIssues(searchSeverity, searchCategory, null);
            relatedIssueTotal = completeRelatedIssues.size();
            relatedIssues = List.copyOf(completeRelatedIssues.subList(
                    0,
                    Math.min(MAX_RELATED_ISSUES, completeRelatedIssues.size())));
        } catch (Exception e) {
            log.warn("Failed to search related issues: {}", e.getMessage());
        }
        
        Map<String, Object> result = new HashMap<>();
        result.put("analysisId", analysisId);
        result.put("question", question);
        result.put("analysis", boundedAnalysisProjection(analysisData));
        if (relatedIssues != null) {
            result.put("relatedIssues", relatedIssues);
            result.put("issueCount", relatedIssues.size());
            boolean relatedComplete = relatedIssueTotal != null
                    && relatedIssueTotal <= MAX_RELATED_ISSUES;
            result.put("relatedIssuesComplete", relatedComplete);
            result.put("relatedIssueTotalCount", relatedIssueTotal);
            result.put("omittedRelatedIssueCount", Math.max(
                    0,
                    relatedIssueTotal - relatedIssues.size()));
            result.put("relatedIssuesStatus", relatedComplete
                    ? "complete"
                    : "bounded_for_mcp_prompt");
            if (!relatedComplete) {
                result.put("relatedIssuesDiagnostic",
                        "Related issue projection reached the MCP prompt limit; "
                                + "additional matches remain available through searchIssues.");
            }
        } else {
            result.put("relatedIssuesComplete", false);
            result.put("relatedIssuesStatus", "unavailable");
            result.put("relatedIssuesDiagnostic",
                    "Related issue search failed; do not interpret this as no matching issues.");
        }
        
        if (includeContext != null && includeContext) {
            result.put("contextIncluded", true);
            result.put("usage", "Use this analysis data along with RAG codebase context to answer the user's question.");
        }
        
        result.put("suggestedApproach", List.of(
            "Review the analysis summary for overall context",
            "Check related issues for specific problems mentioned in the question",
            "Use getIssueDetails tool for more info on specific issues",
            "Combine with RAG codebase context for code-level answers"
        ));
        
        return result;
    }

    private Map<String, Object> boundedAnalysisProjection(
            Map<String, Object> analysisData) {
        Map<String, Object> bounded = new LinkedHashMap<>(analysisData);
        Object rawIssues = bounded.get("issues");
        if (!(rawIssues instanceof List<?> completeIssues)) {
            return bounded;
        }
        int admitted = Math.min(MAX_ANALYSIS_ISSUES, completeIssues.size());
        int omitted = completeIssues.size() - admitted;
        bounded.put("issues", List.copyOf(completeIssues.subList(0, admitted)));
        bounded.put("issueCount", admitted);
        bounded.put("totalIssueCount", completeIssues.size());
        bounded.put("issuesComplete", omitted == 0);
        bounded.put("omittedIssueCount", omitted);
        if (omitted > 0) {
            bounded.put("issuesStatus", "bounded_for_mcp_prompt");
            bounded.put("issuesDiagnostic",
                    "Analysis issue projection reached the MCP prompt limit; "
                            + "omitted issues remain available from the complete analysis API "
                            + "and must not be treated as absent.");
        }
        return bounded;
    }
    
    /**
     * Extract issue category from question keywords.
     */
    private String extractCategoryFromQuestion(String question) {
        String q = question.toLowerCase();
        if (q.contains("security") || q.contains("vulnerability") || q.contains("injection") || q.contains("xss")) {
            return "SECURITY";
        }
        if (q.contains("performance") || q.contains("slow") || q.contains("memory") || q.contains("leak")) {
            return "PERFORMANCE";
        }
        if (q.contains("style") || q.contains("format") || q.contains("naming") || q.contains("convention")) {
            return "CODE_STYLE";
        }
        if (q.contains("bug") || q.contains("error") || q.contains("exception") || q.contains("null")) {
            return "BUG_RISK";
        }
        return null; // No specific category filter
    }
    
    /**
     * Extract severity from question keywords.
     */
    private String extractSeverityFromQuestion(String question) {
        String q = question.toLowerCase();
        if (q.contains("critical") || q.contains("severe") || q.contains("high") || q.contains("important")) {
            return "HIGH";
        }
        if (q.contains("medium") || q.contains("moderate")) {
            return "MEDIUM";
        }
        if (q.contains("low") || q.contains("minor")) {
            return "LOW";
        }
        if (q.contains("info") || q.contains("informational") || q.contains("suggestion") || q.contains("note")) {
            return "INFO";
        }
        return null; // No specific severity filter
    }
    
    private Long getLongArg(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) return null;
        if (value instanceof Long longValue) return longValue;
        if (value instanceof Integer integerValue) return integerValue.longValue();
        if (value instanceof String stringValue) return Long.parseLong(stringValue);
        return null;
    }
    
    private String getStringArg(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) return null;
        return value.toString();
    }
    
    private Boolean getBoolArg(Map<String, Object> args, String key) {
        Object value = args.get(key);
        if (value == null) return false;
        if (value instanceof Boolean booleanValue) return booleanValue;
        if (value instanceof String stringValue) return Boolean.parseBoolean(stringValue);
        return false;
    }
}
