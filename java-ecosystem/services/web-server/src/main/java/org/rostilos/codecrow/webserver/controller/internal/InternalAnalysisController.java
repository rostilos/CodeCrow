package org.rostilos.codecrow.webserver.controller.internal;

import org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.webserver.service.project.ProjectService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.format.DateTimeFormatter;
import java.util.*;

/**
 * Internal API for service-to-service communication.
 * Used by Platform MCP to fetch analysis data.
 * 
 * Security:
 * 1. Network-level: Only accessible from internal Docker network (not exposed to public)
 * 2. Project-scoped: All queries MUST include projectId - data isolation is enforced
 * 3. Origin trust: projectId comes from webhook validation chain (secret key in URL)
 */
@RestController
@RequestMapping("/api/internal/analysis")
public class InternalAnalysisController {
    
    private static final Logger log = LoggerFactory.getLogger(InternalAnalysisController.class);
    private static final int MAX_LIMIT = 100;

    private final CodeAnalysisService codeAnalysisService;
    private final ProjectService projectService;

    public InternalAnalysisController(CodeAnalysisService codeAnalysisService, ProjectService projectService) {
        this.codeAnalysisService = codeAnalysisService;
        this.projectService = projectService;
    }

    /**
     * List analyses for a project - used by Platform MCP listProjectAnalyses tool.
     * 
     * SECURITY: projectId is required and all results are scoped to that project.
     */
    @GetMapping
    public ResponseEntity<?> listAnalyses(
            @RequestParam Long projectId,
            @RequestParam(required = false) Long pullRequestId,
            @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "20") int limit,
            @RequestParam(defaultValue = "0") int offset
    ) {
        log.debug("Internal API: Listing analyses for project {} with limit={}, offset={}", 
                projectId, limit, offset);
        
        // Verify project exists
        try {
            Project project = projectService.getProjectById(projectId);
            if (project == null) {
                return ResponseEntity.notFound().build();
            }
        } catch (Exception e) {
            log.warn("Project {} not found", projectId);
            return ResponseEntity.notFound().build();
        }
        
        // Apply limits
        int effectiveLimit = Math.min(Math.max(limit, 1), MAX_LIMIT);
        int effectiveOffset = Math.max(offset, 0);
        
        // Parse status filter if provided
        AnalysisStatus statusFilter = null;
        if (status != null && !status.isBlank()) {
            try {
                statusFilter = AnalysisStatus.valueOf(status.toUpperCase());
            } catch (IllegalArgumentException e) {
                log.warn("Invalid status filter: {}", status);
            }
        }
        
        // Calculate page number and intra-page offset for arbitrary offset support
        // When offset is not a multiple of limit, we need to skip some records within the page
        int pageNumber = effectiveOffset / effectiveLimit;
        int intraPageOffset = effectiveOffset % effectiveLimit;
        
        // If there's an intra-page offset, we need to fetch more records to fill the requested limit
        int fetchSize = intraPageOffset > 0 ? effectiveLimit + intraPageOffset : effectiveLimit;
        
        // Use paginated repository query for better performance
        Page<CodeAnalysis> page = codeAnalysisService.searchAnalyses(
                projectId,
                pullRequestId,
                statusFilter,
                PageRequest.of(pageNumber, fetchSize, Sort.by(Sort.Direction.DESC, "createdAt")));
        
        // Apply intra-page offset and limit to handle arbitrary offsets correctly
        List<CodeAnalysis> analyses = page.getContent().stream()
                .skip(intraPageOffset)
                .limit(effectiveLimit)
                .toList();
        int totalCount = (int) page.getTotalElements();
        // hasMore is true if there are more records after current offset + returned count
        boolean hasMore = (effectiveOffset + analyses.size()) < totalCount;
        
        // Convert to DTOs
        List<Map<String, Object>> analysisDTOs = analyses.stream()
                .map(this::toAnalysisDTO)
                .toList();
        
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("projectId", projectId);
        result.put("analyses", analysisDTOs);
        result.put("totalCount", totalCount);
        result.put("limit", effectiveLimit);
        result.put("offset", effectiveOffset);
        result.put("hasMore", hasMore);

        return ResponseEntity.ok(result);
    }

    /**
     * Get analysis by ID - used by Platform MCP getAnalysisResults tool.
     * 
     * SECURITY: Requires projectId to ensure caller can only access analyses from their project.
     */
    @GetMapping("/{analysisId}")
    public ResponseEntity<?> getAnalysisById(
            @PathVariable Long analysisId,
            @RequestParam Long projectId
    ) {
        log.debug("Internal API: Fetching analysis {} for project {}", analysisId, projectId);
        
        Optional<CodeAnalysis> optionalAnalysis = codeAnalysisService.findById(analysisId);

        if (optionalAnalysis.isEmpty()) {
            log.debug("Analysis {} not found", analysisId);
            return ResponseEntity.notFound().build();
        }
        
        CodeAnalysis analysis = optionalAnalysis.get();
        
        // SECURITY: Verify analysis belongs to the requested project
        if (analysis.getProject() == null || !analysis.getProject().getId().equals(projectId)) {
            log.warn("Security: Analysis {} does not belong to project {} - access denied", analysisId, projectId);
            return ResponseEntity.status(403)
                    .body(Map.of("error", "Analysis does not belong to the specified project"));
        }
        
        return ResponseEntity.ok(toDetailedAnalysisDTO(analysis));
    }

    /**
     * Get analysis results by PR number - used by Platform MCP getAnalysisResults tool.
     * 
     * Returns the latest analysis for a given PR.
     */
    @GetMapping("/pr/{prNumber}")
    public ResponseEntity<?> getAnalysisByPr(
            @PathVariable Long prNumber,
            @RequestParam Long projectId,
            @RequestParam(required = false) Integer prVersion
    ) {
        log.debug("Internal API: Fetching analysis for project {} PR #{}", projectId, prNumber);
        
        Optional<CodeAnalysis> optionalAnalysis;
        if (prVersion != null) {
            optionalAnalysis = codeAnalysisService.findByProjectIdAndPrNumberAndPrVersion(projectId, prNumber, prVersion);
        } else {
            // Get latest version
            optionalAnalysis = codeAnalysisService.findByProjectIdAndPrNumber(projectId, prNumber);
        }

        if (optionalAnalysis.isEmpty()) {
            log.debug("Analysis for project {} PR #{} not found", projectId, prNumber);
            return ResponseEntity.notFound().build();
        }
        
        CodeAnalysis analysis = optionalAnalysis.get();
        
        return ResponseEntity.ok(toDetailedAnalysisDTO(analysis));
    }

    /**
     * Convert CodeAnalysis to a summary DTO.
     */
    private Map<String, Object> toAnalysisDTO(CodeAnalysis analysis) {
        Map<String, Object> dto = new LinkedHashMap<>();
        dto.put("id", analysis.getId());
        dto.put("prNumber", analysis.getPrNumber());
        dto.put("prVersion", analysis.getPrVersion());
        dto.put("branchName", analysis.getBranchName());
        dto.put("sourceBranchName", analysis.getSourceBranchName());
        dto.put("status", analysis.getStatus() != null ? analysis.getStatus().name() : null);
        dto.put("totalIssues", analysis.getTotalIssues());
        dto.put("resolvedCount", analysis.getResolvedCount());
        dto.put("commitHash", analysis.getCommitHash());
        
        // Severity counts - CodeAnalysis has these pre-calculated
        dto.put("highCount", analysis.getHighSeverityCount());
        dto.put("mediumCount", analysis.getMediumSeverityCount());
        dto.put("lowCount", analysis.getLowSeverityCount());
        
        // Timestamps
        if (analysis.getCreatedAt() != null) {
            dto.put("createdAt", analysis.getCreatedAt().format(DateTimeFormatter.ISO_OFFSET_DATE_TIME));
        }
        if (analysis.getUpdatedAt() != null) {
            dto.put("completedAt", analysis.getUpdatedAt().format(DateTimeFormatter.ISO_OFFSET_DATE_TIME));
        }
        
        return dto;
    }

    /**
     * Convert CodeAnalysis to a detailed DTO including issues.
     */
    private Map<String, Object> toDetailedAnalysisDTO(CodeAnalysis analysis) {
        Map<String, Object> dto = toAnalysisDTO(analysis);
        
        // Add comment if available (used as summary/description)
        dto.put("comment", analysis.getComment());
        
        // Add issues summary (not full list to keep response size reasonable)
        List<CodeAnalysisIssue> issues = analysis.getIssues();
        if (issues != null && !issues.isEmpty()) {
            // Include top issues by severity
            List<Map<String, Object>> topIssues = issues.stream()
                    .sorted(Comparator.comparing((CodeAnalysisIssue i) -> severityOrder(i.getSeverity())))
                    .limit(20)
                    .map(this::toIssuePreview)
                    .toList();
            dto.put("topIssues", topIssues);
            
            // Group by category
            Map<String, Long> categoryBreakdown = issues.stream()
                    .filter(i -> i.getIssueCategory() != null)
                    .collect(java.util.stream.Collectors.groupingBy(
                            i -> i.getIssueCategory().name(),
                            java.util.stream.Collectors.counting()
                    ));
            dto.put("categoryBreakdown", categoryBreakdown);
        }
        
        return dto;
    }

    /**
     * Create a preview of an issue (minimal fields).
     */
    private Map<String, Object> toIssuePreview(CodeAnalysisIssue issue) {
        Map<String, Object> preview = new LinkedHashMap<>();
        preview.put("id", issue.getId());
        preview.put("severity", issue.getSeverity() != null ? issue.getSeverity().name() : null);
        preview.put("category", issue.getIssueCategory() != null ? issue.getIssueCategory().name() : null);
        preview.put("reason", issue.getReason());
        preview.put("filePath", issue.getFilePath());
        preview.put("lineNumber", issue.getLineNumber());
        preview.put("isResolved", issue.isResolved());
        return preview;
    }

    /**
     * Get severity order for sorting (HIGH = 0, MEDIUM = 1, LOW = 2).
     */
    private int severityOrder(IssueSeverity severity) {
        if (severity == null) return 4;
        return switch (severity) {
            case HIGH -> 0;
            case MEDIUM -> 1;
            case LOW -> 2;
            case RESOLVED -> 3;
        };
    }
}
