package org.rostilos.codecrow.webserver.controller.internal;

import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.webserver.service.project.AnalysisService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Optional;

/**
 * Internal API for service-to-service communication.
 * Used by Platform MCP to fetch issue data.
 * 
 * Security:
 * 1. Network-level: Only accessible from internal Docker network (not exposed to public)
 * 2. Project-scoped: All queries MUST include projectId - data isolation is enforced
 * 3. Origin trust: projectId comes from webhook validation chain (secret key in URL)
 * 
 * The security chain:
 * - Webhook arrives with secret key in URL → pipeline-agent validates
 * - pipeline-agent extracts project from DB using the validated key
 * - project.id is passed to mcp-client → platform-mcp → this API
 * - This API enforces that returned data belongs to that project only
 */
@RestController
@RequestMapping("/api/internal/issues")
public class InternalIssueController {
    
    private static final Logger log = LoggerFactory.getLogger(InternalIssueController.class);

    private final AnalysisService analysisService;

    public InternalIssueController(AnalysisService analysisService) {
        this.analysisService = analysisService;
    }

    /**
     * Get issue by ID - used by Platform MCP getIssueDetails tool.
     * 
     * SECURITY: Requires projectId to ensure caller can only access issues from their project.
     * The projectId originates from webhook validation and cannot be forged.
     */
    @GetMapping("/{issueId}")
    public ResponseEntity<?> getIssueById(
            @PathVariable Long issueId,
            @RequestParam Long projectId
    ) {
        log.debug("Internal API: Fetching issue {} for project {}", issueId, projectId);
        
        Optional<CodeAnalysisIssue> optionalIssue = analysisService.findIssueById(issueId);

        if (optionalIssue.isEmpty()) {
            log.debug("Issue {} not found", issueId);
            return ResponseEntity.notFound().build();
        }
        
        CodeAnalysisIssue issue = optionalIssue.get();
        
        // SECURITY: Verify issue belongs to the requested project
        // This prevents access to issues from other projects even if ID is guessed
        if (issue.getAnalysis() == null || 
            issue.getAnalysis().getProject() == null ||
            !issue.getAnalysis().getProject().getId().equals(projectId)) {
            log.warn("Security: Issue {} does not belong to project {} - access denied", issueId, projectId);
            return ResponseEntity.status(HttpStatus.FORBIDDEN)
                    .body("{\"error\": \"Issue does not belong to the specified project\"}");
        }
        
        return ResponseEntity.ok(IssueDTO.fromEntity(issue));
    }

    /**
     * Search issues by project - used by Platform MCP searchIssues tool.
     * 
     * SECURITY: projectId is required and all results are scoped to that project.
     */
    @GetMapping
    public ResponseEntity<?> searchIssues(
            @RequestParam Long projectId,
            @RequestParam(required = false) String severity,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String branch,
            @RequestParam(required = false) String pullRequestId,
            @RequestParam(defaultValue = "50") int limit
    ) {
        log.debug("Internal API: Searching issues for project {} with severity={}, category={}", 
                projectId, severity, category);
        
        // Limit max results to prevent abuse
        int effectiveLimit = Math.min(limit, 200);
        
        List<CodeAnalysisIssue> issues = analysisService.findIssues(
                projectId, branch, pullRequestId, severity, category, 0
        );
        
        List<IssueDTO> issueDTOs = issues.stream()
                .limit(effectiveLimit)
                .map(IssueDTO::fromEntity)
                .toList();

        return ResponseEntity.ok(issueDTOs);
    }
}
