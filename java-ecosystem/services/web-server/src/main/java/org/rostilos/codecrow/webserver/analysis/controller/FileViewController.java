package org.rostilos.codecrow.webserver.analysis.controller;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.security.annotations.IsWorkspaceMember;
import org.rostilos.codecrow.webserver.analysis.dto.response.AnalysisFilesResponse;
import org.rostilos.codecrow.webserver.analysis.dto.response.FileSnippetResponse;
import org.rostilos.codecrow.webserver.analysis.dto.response.FileViewResponse;
import org.rostilos.codecrow.webserver.analysis.service.FileViewService;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.rostilos.codecrow.webserver.workspace.service.WorkspaceService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * REST controller for the source code viewer feature.
 * Provides endpoints to list analyzed files and retrieve file content with inline issue annotations.
 *
 * <p>Endpoints:
 * <ul>
 *   <li>GET  /api/{ws}/project/{ns}/analysis/{analysisId}/files — list all stored files for an analysis</li>
 *   <li>GET  /api/{ws}/project/{ns}/analysis/{analysisId}/file-view?path=... — get file content with issues</li>
 * </ul>
 */
@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping("/api/{workspaceSlug}/project/{projectNamespace}/analysis")
public class FileViewController {

    private static final Logger log = LoggerFactory.getLogger(FileViewController.class);

    private final FileViewService fileViewService;
    private final ProjectService projectService;
    private final WorkspaceService workspaceService;

    public FileViewController(
            FileViewService fileViewService,
            ProjectService projectService,
            WorkspaceService workspaceService
    ) {
        this.fileViewService = fileViewService;
        this.projectService = projectService;
        this.workspaceService = workspaceService;
    }

    /**
     * List all files that have stored content for a given analysis.
     * Returns file metadata with issue counts per file.
     */
    @GetMapping("/{analysisId}/files")
    @IsWorkspaceMember
    public ResponseEntity<AnalysisFilesResponse> listAnalysisFiles(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long analysisId
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        return fileViewService.listAnalysisFiles(project.getId(), analysisId)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    /**
     * Get file content with inline issue annotations for the source code viewer.
     * The response includes the full file content and a list of issues positioned by line number.
     *
     * @param filePath the repo-relative file path (URL-encoded, passed as query parameter)
     */
    @GetMapping("/{analysisId}/file-view")
    @IsWorkspaceMember
    public ResponseEntity<FileViewResponse> getFileView(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long analysisId,
            @RequestParam("path") String filePath
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        return fileViewService.getFileView(project.getId(), analysisId, filePath)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    /**
     * Get a snippet of source code around a specific line, with inline issue annotations.
     * Used for the inline code preview on issue detail pages.
     *
     * <p>Supports two modes:
     * <ul>
     *   <li>Center mode: pass {@code line} + {@code context} to get ±context lines around a center line</li>
     *   <li>Range mode: pass {@code startLine} + {@code endLine} to get an exact line range</li>
     * </ul>
     *
     * @param filePath  the repo-relative file path
     * @param line      the line number to center the snippet around (1-based, optional if startLine/endLine given)
     * @param context   number of context lines above and below (default 10)
     * @param startLine explicit start line (1-based, inclusive), overrides center mode
     * @param endLine   explicit end line (1-based, inclusive), overrides center mode
     */
    @GetMapping("/{analysisId}/file-snippet")
    @IsWorkspaceMember
    public ResponseEntity<FileSnippetResponse> getFileSnippet(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long analysisId,
            @RequestParam("path") String filePath,
            @RequestParam(value = "line", defaultValue = "0") int line,
            @RequestParam(value = "context", defaultValue = "10") int context,
            @RequestParam(value = "startLine", required = false) Integer startLine,
            @RequestParam(value = "endLine", required = false) Integer endLine
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        // Range mode: explicit startLine/endLine
        if (startLine != null && endLine != null) {
            return fileViewService.getFileSnippetByRange(project.getId(), analysisId, filePath, startLine, endLine)
                    .map(ResponseEntity::ok)
                    .orElse(ResponseEntity.notFound().build());
        }

        // Center mode: line ± context
        return fileViewService.getFileSnippet(project.getId(), analysisId, filePath, line, context)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    /**
     * Get the latest analysis ID for a given branch.
     * Used by the branch-level source viewer to determine which analysis to display.
     *
     * @param branchName the branch name (URL-encoded, passed as query parameter)
     * @return JSON with analysisId, or 404 if no analysis exists for this branch
     */
    @GetMapping("/branch-latest")
    @IsWorkspaceMember
    public ResponseEntity<?> getLatestBranchAnalysis(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @RequestParam("branch") String branchName
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        return fileViewService.getLatestBranchAnalysisId(project.getId(), branchName)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    // ── PR-level source code endpoints ───────────────────────────────────

    /**
     * List all accumulated files for a pull request across all analysis iterations.
     * Unlike the analysis-level endpoint, this includes files from <b>every</b> run.
     */
    @GetMapping("/pr/{prNumber}/files")
    @IsWorkspaceMember
    public ResponseEntity<AnalysisFilesResponse> listPrFiles(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long prNumber
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        return fileViewService.listPrFiles(project.getId(), prNumber)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    /**
     * Get file content with inline issues for a PR.
     */
    @GetMapping("/pr/{prNumber}/file-view")
    @IsWorkspaceMember
    public ResponseEntity<FileViewResponse> getPrFileView(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long prNumber,
            @RequestParam("path") String filePath
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        return fileViewService.getPrFileView(project.getId(), prNumber, filePath)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    /**
     * Get a snippet of PR source code around a specific line.
     */
    @GetMapping("/pr/{prNumber}/file-snippet")
    @IsWorkspaceMember
    public ResponseEntity<FileSnippetResponse> getPrFileSnippet(
            @PathVariable String workspaceSlug,
            @PathVariable String projectNamespace,
            @PathVariable Long prNumber,
            @RequestParam("path") String filePath,
            @RequestParam(value = "line", defaultValue = "0") int line,
            @RequestParam(value = "context", defaultValue = "10") int context,
            @RequestParam(value = "startLine", required = false) Integer startLine,
            @RequestParam(value = "endLine", required = false) Integer endLine
    ) {
        Workspace workspace = workspaceService.getWorkspaceBySlug(workspaceSlug);
        Project project = projectService.getProjectByWorkspaceAndNamespace(workspace.getId(), projectNamespace);

        // Range mode
        if (startLine != null && endLine != null) {
            return fileViewService.getPrFileSnippetByRange(project.getId(), prNumber, filePath, startLine, endLine)
                    .map(ResponseEntity::ok)
                    .orElse(ResponseEntity.notFound().build());
        }

        // Center mode
        return fileViewService.getPrFileSnippet(project.getId(), prNumber, filePath, line, context)
                .map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }
}
