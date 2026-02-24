package org.rostilos.codecrow.webserver.analysis.service;

import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.FileSnapshotService;
import org.rostilos.codecrow.webserver.analysis.dto.response.AnalysisFilesResponse;
import org.rostilos.codecrow.webserver.analysis.dto.response.FileSnippetResponse;
import org.rostilos.codecrow.webserver.analysis.dto.response.FileViewResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;
import java.util.stream.Collectors;

/**
 * Service for the source code viewer feature.
 * Retrieves stored file content and overlays issue annotations for display.
 */
@Service
@Transactional(readOnly = true)
public class FileViewService {

    private static final Logger log = LoggerFactory.getLogger(FileViewService.class);

    private final FileSnapshotService fileSnapshotService;
    private final CodeAnalysisService codeAnalysisService;
    private final PullRequestRepository pullRequestRepository;

    public FileViewService(FileSnapshotService fileSnapshotService,
                           CodeAnalysisService codeAnalysisService,
                           PullRequestRepository pullRequestRepository) {
        this.fileSnapshotService = fileSnapshotService;
        this.codeAnalysisService = codeAnalysisService;
        this.pullRequestRepository = pullRequestRepository;
    }

    /**
     * List all files that have stored content for a specific analysis.
     * Used to populate the file tree in the source code viewer.
     *
     * @param projectId the project ID (for authorization context)
     * @param analysisId the analysis ID
     * @return list of file entries with metadata and issue counts
     */
    public Optional<AnalysisFilesResponse> listAnalysisFiles(Long projectId, Long analysisId) {
        Optional<CodeAnalysis> analysisOpt = codeAnalysisService.findById(analysisId);
        if (analysisOpt.isEmpty()) {
            return Optional.empty();
        }
        CodeAnalysis analysis = analysisOpt.get();

        // Verify the analysis belongs to this project
        if (!analysis.getProject().getId().equals(projectId)) {
            return Optional.empty();
        }

        List<AnalyzedFileSnapshot> snapshots = fileSnapshotService.getSnapshots(analysisId);
        if (snapshots.isEmpty()) {
            return Optional.empty();
        }

        // Build issue count map per file — aggregate across ALL analyses on the same branch
        // so the sidebar file tree shows accurate total issue counts
        String branchName = analysis.getBranchName();
        List<CodeAnalysisIssue> issues;
        if (branchName != null && !branchName.isEmpty()) {
            issues = codeAnalysisService.findIssuesByBranch(analysis.getProject().getId(), branchName);
        } else {
            issues = codeAnalysisService.findIssuesByAnalysisId(analysisId);
        }
        Map<String, List<CodeAnalysisIssue>> issuesByFile = issues.stream()
                .filter(i -> i.getFilePath() != null)
                .collect(Collectors.groupingBy(CodeAnalysisIssue::getFilePath));

        List<AnalysisFilesResponse.FileEntry> fileEntries = snapshots.stream()
                .map(snapshot -> {
                    List<CodeAnalysisIssue> fileIssues = issuesByFile.getOrDefault(
                            snapshot.getFilePath(), List.of());
                    long highCount = fileIssues.stream()
                            .filter(i -> !i.isResolved() && i.getSeverity() == IssueSeverity.HIGH).count();
                    long medCount = fileIssues.stream()
                            .filter(i -> !i.isResolved() && i.getSeverity() == IssueSeverity.MEDIUM).count();
                    return new AnalysisFilesResponse.FileEntry(
                            snapshot.getFilePath(),
                            snapshot.getFileContent() != null ? snapshot.getFileContent().getLineCount() : 0,
                            snapshot.getFileContent() != null ? snapshot.getFileContent().getSizeBytes() : 0,
                            (int) fileIssues.stream().filter(i -> !i.isResolved()).count(),
                            (int) highCount,
                            (int) medCount
                    );
                })
                .sorted(Comparator.comparing(AnalysisFilesResponse.FileEntry::filePath))
                .collect(Collectors.toList());

        return Optional.of(new AnalysisFilesResponse(
                analysisId,
                analysis.getCommitHash(),
                analysis.getPrVersion(),
                fileEntries
        ));
    }

    /**
     * Get the file content with inline issue annotations for the source code viewer.
     *
     * @param projectId  the project ID (for authorization)
     * @param analysisId the analysis to view
     * @param filePath   the repo-relative file path
     * @return the file view with content + annotated issues, or empty if not found
     */
    public Optional<FileViewResponse> getFileView(Long projectId, Long analysisId, String filePath) {
        Optional<CodeAnalysis> analysisOpt = codeAnalysisService.findById(analysisId);
        if (analysisOpt.isEmpty()) {
            return Optional.empty();
        }
        CodeAnalysis analysis = analysisOpt.get();

        // Verify the analysis belongs to this project
        if (!analysis.getProject().getId().equals(projectId)) {
            return Optional.empty();
        }

        // Get file content from storage
        Optional<String> contentOpt = fileSnapshotService.getFileContent(analysisId, filePath);
        if (contentOpt.isEmpty()) {
            return Optional.empty();
        }
        String content = contentOpt.get();
        int lineCount = countLines(content);

        // Get issues for this file — aggregate across ALL analyses on the same branch
        // so the source viewer shows every issue, not just those from one particular analysis
        String branchName = analysis.getBranchName();
        List<CodeAnalysisIssue> fileIssues;
        if (branchName != null && !branchName.isEmpty()) {
            fileIssues = codeAnalysisService.findIssuesByBranchAndFilePath(
                    analysis.getProject().getId(), branchName, filePath);
        } else {
            // Fallback for analyses without a branch (shouldn't normally happen)
            fileIssues = codeAnalysisService.findIssuesByAnalysisId(analysisId).stream()
                    .filter(i -> filePath.equals(i.getFilePath()))
                    .collect(Collectors.toList());
        }
        List<FileViewResponse.InlineIssue> inlineIssues = fileIssues.stream()
                .filter(FileViewService::hasTitle)
                .sorted(Comparator.comparingInt(i -> i.getLineNumber() != null ? i.getLineNumber() : 0))
                .map(i -> new FileViewResponse.InlineIssue(
                        i.getId(),
                        i.getLineNumber() != null ? i.getLineNumber() : 0,
                        i.getSeverity() != null ? i.getSeverity().name() : "INFO",
                        i.getTitle(),
                        i.getReason(),
                        i.getIssueCategory() != null ? i.getIssueCategory().name() : null,
                        i.isResolved(),
                        i.getSuggestedFixDescription(),
                        i.getSuggestedFixDiff(),
                        i.getTrackedFromIssueId(),
                        i.getTrackingConfidence()
                ))
                .collect(Collectors.toList());

        // Get commit hash from snapshot
        List<AnalyzedFileSnapshot> snapshots = fileSnapshotService.getSnapshots(analysisId);
        String commitHash = snapshots.stream()
                .filter(s -> filePath.equals(s.getFilePath()))
                .map(AnalyzedFileSnapshot::getCommitHash)
                .findFirst()
                .orElse(analysis.getCommitHash());

        return Optional.of(new FileViewResponse(
                filePath,
                content,
                lineCount,
                commitHash,
                analysisId,
                analysis.getPrVersion(),
                inlineIssues
        ));
    }

    /**
     * Get a snippet of source code around a specific line, with inline issue annotations.
     * Used for the inline code preview on issue detail pages.
     *
     * @param projectId   the project ID (for authorization)
     * @param analysisId  the analysis to view
     * @param filePath    the repo-relative file path
     * @param centerLine  the line to center the snippet around (1-based)
     * @param contextSize number of lines above and below the center line to include
     * @return the snippet with source lines and annotated issues, or empty if not found
     */
    public Optional<FileSnippetResponse> getFileSnippet(
            Long projectId, Long analysisId, String filePath, int centerLine, int contextSize
    ) {
        Optional<CodeAnalysis> analysisOpt = codeAnalysisService.findById(analysisId);
        if (analysisOpt.isEmpty()) {
            return Optional.empty();
        }
        CodeAnalysis analysis = analysisOpt.get();
        if (!analysis.getProject().getId().equals(projectId)) {
            return Optional.empty();
        }

        // Get file content
        Optional<String> contentOpt = fileSnapshotService.getFileContent(analysisId, filePath);
        if (contentOpt.isEmpty()) {
            return Optional.empty();
        }
        String content = contentOpt.get();
        String[] allLines = content.split("\n", -1);
        int totalLineCount = allLines.length;

        // Compute the snippet window (clamped to file bounds)
        int startLine = Math.max(1, centerLine - contextSize);
        int endLine = Math.min(totalLineCount, centerLine + contextSize);

        // Build snippet lines
        List<FileSnippetResponse.SnippetLine> snippetLines = new ArrayList<>();
        for (int i = startLine; i <= endLine; i++) {
            String lineContent = (i - 1 < allLines.length) ? allLines[i - 1] : "";
            snippetLines.add(new FileSnippetResponse.SnippetLine(i, lineContent));
        }

        // Get issues in the snippet window
        List<CodeAnalysisIssue> allIssues = codeAnalysisService.findIssuesByAnalysisId(analysisId);
        int finalStartLine = startLine;
        int finalEndLine = endLine;
        List<FileViewResponse.InlineIssue> inlineIssues = allIssues.stream()
                .filter(i -> filePath.equals(i.getFilePath()))
                .filter(i -> {
                    int ln = i.getLineNumber() != null ? i.getLineNumber() : 0;
                    return ln >= finalStartLine && ln <= finalEndLine;
                })
                .sorted(Comparator.comparingInt(i -> i.getLineNumber() != null ? i.getLineNumber() : 0))
                .map(i -> new FileViewResponse.InlineIssue(
                        i.getId(),
                        i.getLineNumber() != null ? i.getLineNumber() : 0,
                        i.getSeverity() != null ? i.getSeverity().name() : "INFO",
                        i.getTitle(),
                        i.getReason(),
                        i.getIssueCategory() != null ? i.getIssueCategory().name() : null,
                        i.isResolved(),
                        i.getSuggestedFixDescription(),
                        i.getSuggestedFixDiff(),
                        i.getTrackedFromIssueId(),
                        i.getTrackingConfidence()
                ))
                .collect(Collectors.toList());

        return Optional.of(new FileSnippetResponse(
                filePath,
                analysisId,
                startLine,
                endLine,
                totalLineCount,
                snippetLines,
                inlineIssues
        ));
    }

    /**
     * Get a snippet of source code by explicit line range, with inline issue annotations.
     * Used for the expand-up / expand-down feature on issue detail snippets.
     */
    public Optional<FileSnippetResponse> getFileSnippetByRange(
            Long projectId, Long analysisId, String filePath, int requestedStart, int requestedEnd
    ) {
        Optional<CodeAnalysis> analysisOpt = codeAnalysisService.findById(analysisId);
        if (analysisOpt.isEmpty()) {
            return Optional.empty();
        }
        CodeAnalysis analysis = analysisOpt.get();
        if (!analysis.getProject().getId().equals(projectId)) {
            return Optional.empty();
        }

        Optional<String> contentOpt = fileSnapshotService.getFileContent(analysisId, filePath);
        if (contentOpt.isEmpty()) {
            return Optional.empty();
        }
        String content = contentOpt.get();
        String[] allLines = content.split("\n", -1);
        int totalLineCount = allLines.length;

        // Clamp to file bounds
        int startLine = Math.max(1, requestedStart);
        int endLine = Math.min(totalLineCount, requestedEnd);

        List<FileSnippetResponse.SnippetLine> snippetLines = new ArrayList<>();
        for (int i = startLine; i <= endLine; i++) {
            String lineContent = (i - 1 < allLines.length) ? allLines[i - 1] : "";
            snippetLines.add(new FileSnippetResponse.SnippetLine(i, lineContent));
        }

        List<CodeAnalysisIssue> allIssues = codeAnalysisService.findIssuesByAnalysisId(analysisId);
        int finalStart = startLine;
        int finalEnd = endLine;
        List<FileViewResponse.InlineIssue> inlineIssues = allIssues.stream()
                .filter(i -> filePath.equals(i.getFilePath()))
                .filter(i -> {
                    int ln = i.getLineNumber() != null ? i.getLineNumber() : 0;
                    return ln >= finalStart && ln <= finalEnd;
                })
                .sorted(Comparator.comparingInt(i -> i.getLineNumber() != null ? i.getLineNumber() : 0))
                .map(i -> new FileViewResponse.InlineIssue(
                        i.getId(),
                        i.getLineNumber() != null ? i.getLineNumber() : 0,
                        i.getSeverity() != null ? i.getSeverity().name() : "INFO",
                        i.getTitle(),
                        i.getReason(),
                        i.getIssueCategory() != null ? i.getIssueCategory().name() : null,
                        i.isResolved(),
                        i.getSuggestedFixDescription(),
                        i.getSuggestedFixDiff(),
                        i.getTrackedFromIssueId(),
                        i.getTrackingConfidence()
                ))
                .collect(Collectors.toList());

        return Optional.of(new FileSnippetResponse(
                filePath,
                analysisId,
                startLine,
                endLine,
                totalLineCount,
                snippetLines,
                inlineIssues
        ));
    }

    // ── PR-level source code viewer methods ────────────────────────────

    /**
     * List all accumulated files for a pull request across all analysis iterations.
     */
    public Optional<AnalysisFilesResponse> listPrFiles(Long projectId, Long prNumber) {
        Optional<PullRequest> prOpt = pullRequestRepository.findByPrNumberAndProject_id(prNumber, projectId);
        if (prOpt.isEmpty()) {
            return Optional.empty();
        }
        PullRequest pr = prOpt.get();

        List<AnalyzedFileSnapshot> snapshots = fileSnapshotService.getSnapshotsForPr(pr.getId());
        if (snapshots.isEmpty()) {
            return Optional.empty();
        }

        // Aggregate issues across all analyses for this PR number
        List<CodeAnalysisIssue> issues = codeAnalysisService.findIssuesByPrNumber(projectId, prNumber);
        Map<String, List<CodeAnalysisIssue>> issuesByFile = issues.stream()
                .filter(i -> i.getFilePath() != null)
                .filter(FileViewService::hasTitle)
                .collect(Collectors.groupingBy(CodeAnalysisIssue::getFilePath));

        List<AnalysisFilesResponse.FileEntry> fileEntries = snapshots.stream()
                .map(snapshot -> {
                    List<CodeAnalysisIssue> fileIssues = issuesByFile.getOrDefault(
                            snapshot.getFilePath(), List.of());
                    long highCount = fileIssues.stream()
                            .filter(i -> !i.isResolved() && i.getSeverity() == IssueSeverity.HIGH).count();
                    long medCount = fileIssues.stream()
                            .filter(i -> !i.isResolved() && i.getSeverity() == IssueSeverity.MEDIUM).count();
                    return new AnalysisFilesResponse.FileEntry(
                            snapshot.getFilePath(),
                            snapshot.getFileContent() != null ? snapshot.getFileContent().getLineCount() : 0,
                            snapshot.getFileContent() != null ? snapshot.getFileContent().getSizeBytes() : 0,
                            (int) fileIssues.stream().filter(i -> !i.isResolved()).count(),
                            (int) highCount,
                            (int) medCount
                    );
                })
                .sorted(Comparator.comparing(AnalysisFilesResponse.FileEntry::filePath))
                .collect(Collectors.toList());

        // Use null/0 for analysisId since this is PR-scoped
        return Optional.of(new AnalysisFilesResponse(
                null,
                pr.getCommitHash(),
                null,
                fileEntries
        ));
    }

    /**
     * Get file content with inline issue annotations for a PR.
     */
    public Optional<FileViewResponse> getPrFileView(Long projectId, Long prNumber, String filePath) {
        Optional<PullRequest> prOpt = pullRequestRepository.findByPrNumberAndProject_id(prNumber, projectId);
        if (prOpt.isEmpty()) {
            return Optional.empty();
        }
        PullRequest pr = prOpt.get();

        Optional<String> contentOpt = fileSnapshotService.getFileContentForPr(pr.getId(), filePath);
        if (contentOpt.isEmpty()) {
            return Optional.empty();
        }
        String content = contentOpt.get();
        int lineCount = countLines(content);

        // Aggregate issues across all analyses for this PR number
        List<CodeAnalysisIssue> fileIssues = codeAnalysisService.findIssuesByPrNumberAndFilePath(projectId, prNumber, filePath);
        List<FileViewResponse.InlineIssue> inlineIssues = fileIssues.stream()
                .filter(FileViewService::hasTitle)
                .sorted(Comparator.comparingInt(i -> i.getLineNumber() != null ? i.getLineNumber() : 0))
                .map(i -> new FileViewResponse.InlineIssue(
                        i.getId(),
                        i.getLineNumber() != null ? i.getLineNumber() : 0,
                        i.getSeverity() != null ? i.getSeverity().name() : "INFO",
                        i.getTitle(),
                        i.getReason(),
                        i.getIssueCategory() != null ? i.getIssueCategory().name() : null,
                        i.isResolved(),
                        i.getSuggestedFixDescription(),
                        i.getSuggestedFixDiff(),
                        i.getTrackedFromIssueId(),
                        i.getTrackingConfidence()
                ))
                .collect(Collectors.toList());

        return Optional.of(new FileViewResponse(
                filePath,
                content,
                lineCount,
                pr.getCommitHash(),
                null,      // no single analysisId — this is PR-scoped
                null,
                inlineIssues
        ));
    }

    /**
     * Get a snippet of PR source code around a specific line.
     */
    public Optional<FileSnippetResponse> getPrFileSnippet(
            Long projectId, Long prNumber, String filePath, int centerLine, int contextSize
    ) {
        Optional<PullRequest> prOpt = pullRequestRepository.findByPrNumberAndProject_id(prNumber, projectId);
        if (prOpt.isEmpty()) {
            return Optional.empty();
        }
        PullRequest pr = prOpt.get();

        Optional<String> contentOpt = fileSnapshotService.getFileContentForPr(pr.getId(), filePath);
        if (contentOpt.isEmpty()) {
            return Optional.empty();
        }
        String content = contentOpt.get();
        String[] allLines = content.split("\n", -1);
        int totalLineCount = allLines.length;

        int startLine = Math.max(1, centerLine - contextSize);
        int endLine = Math.min(totalLineCount, centerLine + contextSize);

        List<FileSnippetResponse.SnippetLine> snippetLines = new ArrayList<>();
        for (int i = startLine; i <= endLine; i++) {
            String lineContent = (i - 1 < allLines.length) ? allLines[i - 1] : "";
            snippetLines.add(new FileSnippetResponse.SnippetLine(i, lineContent));
        }

        // Get issues for this PR and file, filtered to the snippet range
        List<CodeAnalysisIssue> allIssues = codeAnalysisService.findIssuesByPrNumberAndFilePath(projectId, prNumber, filePath);
        int finalStartLine = startLine;
        int finalEndLine = endLine;
        List<FileViewResponse.InlineIssue> inlineIssues = allIssues.stream()
                .filter(i -> {
                    int ln = i.getLineNumber() != null ? i.getLineNumber() : 0;
                    return ln >= finalStartLine && ln <= finalEndLine;
                })
                .sorted(Comparator.comparingInt(i -> i.getLineNumber() != null ? i.getLineNumber() : 0))
                .map(i -> new FileViewResponse.InlineIssue(
                        i.getId(),
                        i.getLineNumber() != null ? i.getLineNumber() : 0,
                        i.getSeverity() != null ? i.getSeverity().name() : "INFO",
                        i.getTitle(),
                        i.getReason(),
                        i.getIssueCategory() != null ? i.getIssueCategory().name() : null,
                        i.isResolved(),
                        i.getSuggestedFixDescription(),
                        i.getSuggestedFixDiff(),
                        i.getTrackedFromIssueId(),
                        i.getTrackingConfidence()
                ))
                .collect(Collectors.toList());

        return Optional.of(new FileSnippetResponse(
                filePath,
                null,      // no single analysisId
                startLine,
                endLine,
                totalLineCount,
                snippetLines,
                inlineIssues
        ));
    }

    /**
     * Get a snippet of PR source code by explicit line range.
     */
    public Optional<FileSnippetResponse> getPrFileSnippetByRange(
            Long projectId, Long prNumber, String filePath, int requestedStart, int requestedEnd
    ) {
        Optional<PullRequest> prOpt = pullRequestRepository.findByPrNumberAndProject_id(prNumber, projectId);
        if (prOpt.isEmpty()) {
            return Optional.empty();
        }
        PullRequest pr = prOpt.get();

        Optional<String> contentOpt = fileSnapshotService.getFileContentForPr(pr.getId(), filePath);
        if (contentOpt.isEmpty()) {
            return Optional.empty();
        }
        String content = contentOpt.get();
        String[] allLines = content.split("\n", -1);
        int totalLineCount = allLines.length;

        int startLine = Math.max(1, requestedStart);
        int endLine = Math.min(totalLineCount, requestedEnd);

        List<FileSnippetResponse.SnippetLine> snippetLines = new ArrayList<>();
        for (int i = startLine; i <= endLine; i++) {
            String lineContent = (i - 1 < allLines.length) ? allLines[i - 1] : "";
            snippetLines.add(new FileSnippetResponse.SnippetLine(i, lineContent));
        }

        List<CodeAnalysisIssue> allIssues = codeAnalysisService.findIssuesByPrNumberAndFilePath(projectId, prNumber, filePath);
        int finalStart = startLine;
        int finalEnd = endLine;
        List<FileViewResponse.InlineIssue> inlineIssues = allIssues.stream()
                .filter(i -> {
                    int ln = i.getLineNumber() != null ? i.getLineNumber() : 0;
                    return ln >= finalStart && ln <= finalEnd;
                })
                .sorted(Comparator.comparingInt(i -> i.getLineNumber() != null ? i.getLineNumber() : 0))
                .map(i -> new FileViewResponse.InlineIssue(
                        i.getId(),
                        i.getLineNumber() != null ? i.getLineNumber() : 0,
                        i.getSeverity() != null ? i.getSeverity().name() : "INFO",
                        i.getTitle(),
                        i.getReason(),
                        i.getIssueCategory() != null ? i.getIssueCategory().name() : null,
                        i.isResolved(),
                        i.getSuggestedFixDescription(),
                        i.getSuggestedFixDiff(),
                        i.getTrackedFromIssueId(),
                        i.getTrackingConfidence()
                ))
                .collect(Collectors.toList());

        return Optional.of(new FileSnippetResponse(
                filePath,
                null,
                startLine,
                endLine,
                totalLineCount,
                snippetLines,
                inlineIssues
        ));
    }

    /**
     * Get the latest analysis ID for a given branch.
     * Returns the analysis ID, branch name, commit hash, and creation timestamp.
     */
    public Optional<Map<String, Object>> getLatestBranchAnalysisId(Long projectId, String branchName) {
        return codeAnalysisService.findLatestByProjectIdAndBranch(projectId, branchName)
                .map(analysis -> {
                    Map<String, Object> result = new LinkedHashMap<>();
                    result.put("analysisId", analysis.getId());
                    result.put("branchName", analysis.getBranchName());
                    result.put("commitHash", analysis.getCommitHash());
                    result.put("createdAt", analysis.getCreatedAt() != null ? analysis.getCreatedAt().toString() : null);
                    return result;
                });
    }

    // ── Branch-level source code viewer methods ────────────────────────

    /**
     * List all files for a branch, aggregated across all analyses.
     * Returns the latest version of each file from the most recent analysis.
     * Used to populate the file tree in the branch source code viewer.
     */
    public Optional<AnalysisFilesResponse> listBranchFiles(Long projectId, String branchName) {
        List<AnalyzedFileSnapshot> snapshots = fileSnapshotService.getSnapshotsForBranch(projectId, branchName);
        if (snapshots.isEmpty()) {
            return Optional.empty();
        }

        // Aggregate issues across ALL analyses on this branch
        List<CodeAnalysisIssue> issues = codeAnalysisService.findIssuesByBranch(projectId, branchName);
        Map<String, List<CodeAnalysisIssue>> issuesByFile = issues.stream()
                .filter(i -> i.getFilePath() != null)
                .filter(FileViewService::hasTitle)
                .collect(Collectors.groupingBy(CodeAnalysisIssue::getFilePath));

        List<AnalysisFilesResponse.FileEntry> fileEntries = snapshots.stream()
                .map(snapshot -> {
                    List<CodeAnalysisIssue> fileIssues = issuesByFile.getOrDefault(
                            snapshot.getFilePath(), List.of());
                    long highCount = fileIssues.stream()
                            .filter(i -> !i.isResolved() && i.getSeverity() == IssueSeverity.HIGH).count();
                    long medCount = fileIssues.stream()
                            .filter(i -> !i.isResolved() && i.getSeverity() == IssueSeverity.MEDIUM).count();
                    return new AnalysisFilesResponse.FileEntry(
                            snapshot.getFilePath(),
                            snapshot.getFileContent() != null ? snapshot.getFileContent().getLineCount() : 0,
                            snapshot.getFileContent() != null ? snapshot.getFileContent().getSizeBytes() : 0,
                            (int) fileIssues.stream().filter(i -> !i.isResolved()).count(),
                            (int) highCount,
                            (int) medCount
                    );
                })
                .sorted(Comparator.comparing(AnalysisFilesResponse.FileEntry::filePath))
                .collect(Collectors.toList());

        // Get commit hash from the most recent snapshot
        String commitHash = snapshots.stream()
                .filter(s -> s.getCommitHash() != null)
                .map(AnalyzedFileSnapshot::getCommitHash)
                .findFirst()
                .orElse(null);

        return Optional.of(new AnalysisFilesResponse(
                null,         // no single analysisId — this is branch-scoped
                commitHash,
                null,
                fileEntries
        ));
    }

    /**
     * Get file content with inline issue annotations for a branch.
     * Uses the latest version of the file from the most recent analysis.
     */
    public Optional<FileViewResponse> getBranchFileView(Long projectId, String branchName, String filePath) {
        Optional<String> contentOpt = fileSnapshotService.getFileContentForBranch(projectId, branchName, filePath);
        if (contentOpt.isEmpty()) {
            return Optional.empty();
        }
        String content = contentOpt.get();
        int lineCount = countLines(content);

        // Get issues for this file — aggregate across ALL analyses on this branch
        List<CodeAnalysisIssue> fileIssues = codeAnalysisService.findIssuesByBranchAndFilePath(
                projectId, branchName, filePath);
        List<FileViewResponse.InlineIssue> inlineIssues = fileIssues.stream()
                .filter(FileViewService::hasTitle)
                .sorted(Comparator.comparingInt(i -> i.getLineNumber() != null ? i.getLineNumber() : 0))
                .map(i -> new FileViewResponse.InlineIssue(
                        i.getId(),
                        i.getLineNumber() != null ? i.getLineNumber() : 0,
                        i.getSeverity() != null ? i.getSeverity().name() : "INFO",
                        i.getTitle(),
                        i.getReason(),
                        i.getIssueCategory() != null ? i.getIssueCategory().name() : null,
                        i.isResolved(),
                        i.getSuggestedFixDescription(),
                        i.getSuggestedFixDiff(),
                        i.getTrackedFromIssueId(),
                        i.getTrackingConfidence()
                ))
                .collect(Collectors.toList());

        // Get commit hash from the snapshot
        List<AnalyzedFileSnapshot> branchSnapshots = fileSnapshotService.getSnapshotsForBranch(projectId, branchName);
        String commitHash = branchSnapshots.stream()
                .filter(s -> filePath.equals(s.getFilePath()) && s.getCommitHash() != null)
                .map(AnalyzedFileSnapshot::getCommitHash)
                .findFirst()
                .orElse(null);

        return Optional.of(new FileViewResponse(
                filePath,
                content,
                lineCount,
                commitHash,
                null,      // no single analysisId — this is branch-scoped
                null,
                inlineIssues
        ));
    }

    /**
     * Get a snippet of branch source code around a specific line.
     */
    public Optional<FileSnippetResponse> getBranchFileSnippet(
            Long projectId, String branchName, String filePath, int centerLine, int contextSize
    ) {
        Optional<String> contentOpt = fileSnapshotService.getFileContentForBranch(projectId, branchName, filePath);
        if (contentOpt.isEmpty()) {
            return Optional.empty();
        }
        String content = contentOpt.get();
        String[] allLines = content.split("\n", -1);
        int totalLineCount = allLines.length;

        int startLine = Math.max(1, centerLine - contextSize);
        int endLine = Math.min(totalLineCount, centerLine + contextSize);

        List<FileSnippetResponse.SnippetLine> snippetLines = new ArrayList<>();
        for (int i = startLine; i <= endLine; i++) {
            String lineContent = (i - 1 < allLines.length) ? allLines[i - 1] : "";
            snippetLines.add(new FileSnippetResponse.SnippetLine(i, lineContent));
        }

        List<CodeAnalysisIssue> fileIssues = codeAnalysisService.findIssuesByBranchAndFilePath(
                projectId, branchName, filePath);
        int finalStartLine = startLine;
        int finalEndLine = endLine;
        List<FileViewResponse.InlineIssue> inlineIssues = fileIssues.stream()
                .filter(i -> {
                    int ln = i.getLineNumber() != null ? i.getLineNumber() : 0;
                    return ln >= finalStartLine && ln <= finalEndLine;
                })
                .sorted(Comparator.comparingInt(i -> i.getLineNumber() != null ? i.getLineNumber() : 0))
                .map(i -> new FileViewResponse.InlineIssue(
                        i.getId(),
                        i.getLineNumber() != null ? i.getLineNumber() : 0,
                        i.getSeverity() != null ? i.getSeverity().name() : "INFO",
                        i.getTitle(),
                        i.getReason(),
                        i.getIssueCategory() != null ? i.getIssueCategory().name() : null,
                        i.isResolved(),
                        i.getSuggestedFixDescription(),
                        i.getSuggestedFixDiff(),
                        i.getTrackedFromIssueId(),
                        i.getTrackingConfidence()
                ))
                .collect(Collectors.toList());

        return Optional.of(new FileSnippetResponse(
                filePath,
                null,      // no single analysisId — this is branch-scoped
                startLine,
                endLine,
                totalLineCount,
                snippetLines,
                inlineIssues
        ));
    }

    /**
     * Get source code availability for a project.
     * Returns which branches and PR numbers have stored file snapshots.
     */
    public Map<String, Object> getSourceAvailability(Long projectId) {
        List<String> branches = fileSnapshotService.getBranchesWithSnapshots(projectId);
        List<Long> prNumbers = fileSnapshotService.getPrNumbersWithSnapshots(projectId);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("branches", branches);
        result.put("prNumbers", prNumbers);
        return result;
    }

    /**
     * Returns true if the issue has a non-blank title.
     * Legacy issues created before the title field was introduced lack titles
     * and are excluded from the source code viewer to avoid noisy annotations.
     */
    private static boolean hasTitle(CodeAnalysisIssue issue) {
        return issue.getTitle() != null && !issue.getTitle().isBlank();
    }

    private static int countLines(String content) {
        if (content == null || content.isEmpty()) return 0;
        int lines = 1;
        for (int i = 0; i < content.length(); i++) {
            if (content.charAt(i) == '\n') lines++;
        }
        return lines;
    }
}
