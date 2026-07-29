package org.rostilos.codecrow.webserver.internal.controller;

import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.io.IOException;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Authenticated internal endpoint for isolated product-path evaluation.
 *
 * <p>The web security layer requires {@code X-Internal-Secret} for every
 * {@code /api/internal/**} request. This endpoint applies the same final-result
 * validation and Java issue finalization used by normal PR analysis, but never
 * creates an analysis, reads previous issue state, persists snapshots, or
 * publishes a VCS result.</p>
 */
@RestController
@RequestMapping("/api/internal/analysis")
public class InternalBenchmarkAnalysisController {

    private static final String RESULT_KIND =
            "codecrow-isolated-analysis-finalization";

    private final CodeAnalysisService codeAnalysisService;

    public InternalBenchmarkAnalysisController(
            CodeAnalysisService codeAnalysisService
    ) {
        this.codeAnalysisService = codeAnalysisService;
    }

    @PostMapping("/benchmark-finalize")
    public ResponseEntity<Map<String, Object>> finalizeBenchmarkAnalysis(
            @RequestBody BenchmarkFinalizationRequest request
    ) {
        if (request == null || request.analysisData() == null) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "analysisData is required");
        }

        Map<String, Object> analysisData;
        try {
            analysisData = AiAnalysisClient.validateAnalysisData(
                    request.analysisData());
        } catch (IOException error) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    error.getMessage(),
                    error);
        }

        Object commentValue = analysisData.get("comment");
        if (commentValue != null && !(commentValue instanceof String)) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "analysis comment must be a string or null");
        }

        Map<String, String> fileContents = request.fileContents() != null
                ? request.fileContents()
                : Collections.emptyMap();
        List<CodeAnalysisIssue> finalized =
                codeAnalysisService.finalizeIssuesWithoutPersistence(
                        analysisData,
                        fileContents);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("kind", RESULT_KIND);
        result.put(
                "comment",
                commentValue != null ? commentValue : "No comment provided");
        result.put("rawIssueCount", issueCount(analysisData.get("issues")));
        result.put("finalIssueCount", finalized.size());
        result.put(
                "issues",
                finalized.stream()
                        .map(InternalBenchmarkAnalysisController::toIssue)
                        .toList());
        result.put("analysisDataValidated", true);
        result.put("persisted", false);
        result.put("published", false);
        result.put("previousIssueStateUsed", false);
        return ResponseEntity.ok(result);
    }

    private static int issueCount(Object issues) {
        if (issues instanceof List<?> list) {
            return list.size();
        }
        if (issues instanceof Map<?, ?> map) {
            return map.size();
        }
        return 0;
    }

    private static Map<String, Object> toIssue(CodeAnalysisIssue issue) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("file", issue.getFilePath());
        value.put("line", issue.getLineNumber());
        value.put("endLine", issue.getEndLineNumber());
        value.put("scopeStartLine", issue.getScopeStartLine());
        value.put(
                "scope",
                issue.getIssueScope() != null
                        ? issue.getIssueScope().name()
                        : null);
        value.put("title", issue.getTitle());
        value.put("reason", issue.getReason());
        value.put(
                "category",
                issue.getIssueCategory() != null
                        ? issue.getIssueCategory().name()
                        : null);
        value.put(
                "severity",
                issue.getSeverity() != null
                        ? issue.getSeverity().name()
                        : null);
        value.put(
                "suggestedFixDescription",
                issue.getSuggestedFixDescription());
        value.put("suggestedFixDiff", issue.getSuggestedFixDiff());
        value.put("codeSnippet", issue.getCodeSnippet());
        value.put("lineHash", issue.getLineHash());
        value.put("lineHashContext", issue.getLineHashContext());
        value.put("issueFingerprint", issue.getIssueFingerprint());
        value.put("contentFingerprint", issue.getContentFingerprint());
        value.put("isResolved", issue.isResolved());
        value.put(
                "detectionSource",
                issue.getDetectionSource() != null
                        ? issue.getDetectionSource().name()
                        : null);
        return value;
    }

    public record BenchmarkFinalizationRequest(
            Map<String, Object> analysisData,
            Map<String, String> fileContents
    ) {
    }
}
