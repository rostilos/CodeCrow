package org.rostilos.codecrow.pipelineagent.gitlab.service;

import com.fasterxml.jackson.databind.JsonNode;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.core.util.tracking.DiffSanitizer;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.rostilos.codecrow.vcsclient.bitbucket.service.ReportGenerator;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.util.List;
import java.util.Map;

/**
 * GitLab implementation of VcsReportingService.
 * Posts analysis results as MR comments.
 */
@Service
public class GitLabReportingService implements VcsReportingService {
    private static final Logger log = LoggerFactory.getLogger(GitLabReportingService.class);
    
    /**
     * Marker text used to identify CodeCrow comments for deletion.
     * This should be unique enough to not match user comments.
     */
    private static final String CODECROW_INLINE_MARKER = "<!-- codecrow-inline-issue -->";
    private static final String CODECROW_COMMENT_MARKER = "<!-- codecrow-analysis-comment -->";

    private final ReportGenerator reportGenerator;
    private final VcsClientProvider vcsClientProvider;
    private final VcsRepoBindingRepository vcsRepoBindingRepository;

    public GitLabReportingService(
            ReportGenerator reportGenerator,
            VcsClientProvider vcsClientProvider,
            VcsRepoBindingRepository vcsRepoBindingRepository
    ) {
        this.reportGenerator = reportGenerator;
        this.vcsClientProvider = vcsClientProvider;
        this.vcsRepoBindingRepository = vcsRepoBindingRepository;
    }

    @Override
    public EVcsProvider getProvider() {
        return EVcsProvider.GITLAB;
    }

    private VcsRepoInfo getVcsRepoInfo(Project project) {
        // Use unified method that prefers VcsRepoBinding over legacy vcsBinding
        VcsRepoInfo vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null) {
            return vcsInfo;
        }

        // Fallback to repository lookup (shouldn't be needed normally)
        VcsRepoBinding vcsRepoBinding = vcsRepoBindingRepository.findByProject_Id(project.getId())
                .orElseThrow(() -> new IllegalStateException(
                        "No VCS binding found for project " + project.getId() +
                        ". Neither ProjectVcsConnectionBinding nor VcsRepoBinding is configured."
                ));

        log.debug("Using VcsRepoBinding repository fallback for project {}: {}/{}",
                project.getId(), vcsRepoBinding.getRepoWorkspace(), vcsRepoBinding.getRepoSlug());

        return vcsRepoBinding;
    }

    @Override
    @Transactional(readOnly = true)
    public void postAnalysisResults(
            CodeAnalysis codeAnalysis,
            Project project,
            Long mergeRequestIid,
            Long platformMrEntityId
    ) throws IOException {
        postAnalysisResults(codeAnalysis, project, mergeRequestIid, platformMrEntityId, null);
    }
    
    @Override
    @Transactional(readOnly = true)
    public void postAnalysisResults(
            CodeAnalysis codeAnalysis,
            Project project,
            Long mergeRequestIid,
            Long platformMrEntityId,
            String placeholderCommentId
    ) throws IOException {

        log.info("Posting analysis results to GitLab for MR {} (placeholderCommentId={}, projectId={}, analysisId={})", 
            mergeRequestIid, placeholderCommentId, project.getId(), codeAnalysis.getId());

        AnalysisSummary summary = reportGenerator.createAnalysisSummary(codeAnalysis, platformMrEntityId);
        log.debug("Created analysis summary: {} total issues, {} unresolved", 
            summary.getTotalIssues(), summary.getTotalUnresolvedIssues());
        
        // Use GitLab-specific markdown with collapsible sections for suggested fixes
        String markdownSummary = reportGenerator.createMarkdownSummary(codeAnalysis, summary, true);
        log.debug("Generated markdown summary: {} characters", markdownSummary != null ? markdownSummary.length() : 0);

        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        log.info("VCS repo info: namespace={}, repoSlug={}, connectionId={}", 
            vcsRepoInfo.getRepoWorkspace(), vcsRepoInfo.getRepoSlug(), 
            vcsRepoInfo.getVcsConnection() != null ? vcsRepoInfo.getVcsConnection().getId() : "null");

        GitLabClient client = getClient(vcsRepoInfo);

        // Post or update MR comment with detailed analysis
        postOrUpdateComment(client, vcsRepoInfo, mergeRequestIid, markdownSummary, placeholderCommentId);
        
        // Post inline comments on specific lines (like Bitbucket annotations)
        postInlineComments(client, vcsRepoInfo, mergeRequestIid, codeAnalysis, summary);
        postReviewCheckSummary(client, vcsRepoInfo, mergeRequestIid,
                reportGenerator.createReviewChecksMarkdown(codeAnalysis, platformMrEntityId));

        log.info("Successfully posted analysis results to GitLab for MR {}", mergeRequestIid);
    }
    
    /**
     * Posts inline comments (discussions) on specific lines in the MR diff.
     * Similar to Bitbucket's annotations feature.
     */
    private void postInlineComments(
            GitLabClient client,
            VcsRepoInfo vcsRepoInfo,
            Long mergeRequestIid,
            CodeAnalysis codeAnalysis,
            AnalysisSummary summary
    ) {
        List<AnalysisSummary.IssueSummary> issues = summary.getIssues();
        try {
            if (issues == null || issues.isEmpty()) {
                cleanupInlineComments(client, vcsRepoInfo, mergeRequestIid);
                return;
            }
            // Get MR metadata for diff refs (base_sha, head_sha, start_sha)
            JsonNode mrData = client.getMergeRequest(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    mergeRequestIid
            );
            
            // Extract diff refs from MR metadata
            JsonNode diffRefs = mrData.get("diff_refs");
            if (diffRefs == null) {
                log.warn("Cannot post inline comments: MR diff_refs not available");
                return;
            }
            
            String baseSha = diffRefs.has("base_sha") ? diffRefs.get("base_sha").asText() : null;
            String headSha = diffRefs.has("head_sha") ? diffRefs.get("head_sha").asText() : null;
            String startSha = diffRefs.has("start_sha") ? diffRefs.get("start_sha").asText() : null;
            
            if (baseSha == null || headSha == null || startSha == null) {
                log.warn("Cannot post inline comments: Missing diff refs (base={}, head={}, start={})", 
                    baseSha, headSha, startSha);
                return;
            }
            
            log.debug("MR diff refs: base={}, head={}, start={}", baseSha, headSha, startSha);
            
            cleanupInlineComments(client, vcsRepoInfo, mergeRequestIid);
            // Limit number of inline comments to avoid spam
            int maxInlineComments = 20;
            int posted = 0;
            
            for (AnalysisSummary.IssueSummary issue : issues) {
                if (posted >= maxInlineComments) {
                    log.info("Reached max inline comments limit ({}), remaining issues only in summary", maxInlineComments);
                    break;
                }
                
                String filePath = issue.getFilePath();
                Integer lineNumber = issue.getLineNumber();
                
                // Skip issues without valid file/line info
                if (filePath == null || filePath.isBlank() || lineNumber == null || lineNumber <= 0) {
                    continue;
                }
                
                // Remove leading slash if present
                if (filePath.startsWith("/")) {
                    filePath = filePath.substring(1);
                }
                
                // Build the inline comment body
                String body = buildInlineCommentBody(issue);
                
                try {
                    client.postMergeRequestLineComment(
                            vcsRepoInfo.getRepoWorkspace(),
                            vcsRepoInfo.getRepoSlug(),
                            mergeRequestIid,
                            body,
                            baseSha,
                            headSha,
                            startSha,
                            filePath,
                            lineNumber,
                            issue.getAnchorSide()
                    );
                    posted++;
                    log.debug("Posted inline comment on {}:{}", filePath, lineNumber);
                } catch (Exception e) {
                    // Don't fail the whole operation for individual inline comment failures
                    log.warn("Failed to post inline comment on {}:{} - {}", filePath, lineNumber, e.getMessage());
                }
            }
            
            log.info("Posted {} inline comments on GitLab MR {}", posted, mergeRequestIid);
            
        } catch (Exception e) {
            // Don't fail the whole operation if inline comments fail
            log.warn("Failed to post inline comments: {}", e.getMessage());
        }
    }
    
    private void cleanupInlineComments(GitLabClient client, VcsRepoInfo repository, Long prNumber) throws IOException {
        var discussions = client.listMergeRequestDiscussions(repository.getRepoWorkspace(), repository.getRepoSlug(), prNumber);
        for (var discussion : discussions) {
            for (var note : discussion.path("notes")) {
                String body = note.path("body").asText();
                boolean inline = "DiffNote".equals(note.path("type").asText()) || note.hasNonNull("position");
                if (inline && (body.contains(CODECROW_INLINE_MARKER) || body.contains(CODECROW_COMMENT_MARKER))) {
                    client.deleteMergeRequestDiscussionNote(repository.getRepoWorkspace(), repository.getRepoSlug(),
                            prNumber, discussion.path("id").asText(), note.path("id").asLong());
                }
            }
        }
    }

    /**
     * Builds the body content for an inline comment.
     */
    private String buildInlineCommentBody(AnalysisSummary.IssueSummary issue) {
        StringBuilder body = new StringBuilder();
        
        // Severity emoji
        String severityEmoji = switch (issue.getSeverity()) {
            case HIGH -> "🔴";
            case MEDIUM -> "🟡";
            case LOW -> "🔵";
            default -> "ℹ️";
        };
        
        body.append(severityEmoji).append(" **").append(issue.getSeverity()).append("**");
        
        if (issue.getCategory() != null && !issue.getCategory().isBlank()) {
            body.append(" | ").append(issue.getCategory());
        }
        
        body.append("\n\n");
        // Use title as bold header if available, then reason as detailed description
        if (issue.getTitle() != null && !issue.getTitle().isBlank()) {
            body.append("**").append(issue.getTitle()).append("**\n\n");
        }
        body.append(issue.getReason());
        
        // Add suggested fix if available
        if (DiffSanitizer.hasRealFixDescription(issue.getSuggestedFix())) {
            body.append("\n\n<details>\n<summary>💡 Suggested Fix</summary>\n\n");
            body.append(issue.getSuggestedFix());
            
            // Add diff if available
            if (DiffSanitizer.isValidDiffFormat(issue.getSuggestedFixDiff())) {
                body.append("\n\n```diff\n").append(issue.getSuggestedFixDiff()).append("\n```");
            }
            
            body.append("\n</details>");
        } else if (DiffSanitizer.isValidDiffFormat(issue.getSuggestedFixDiff())) {
            // Diff available without fix description — show diff standalone
            body.append("\n\n<details>\n<summary>💡 Suggested Code Change</summary>\n\n");
            body.append("```diff\n").append(issue.getSuggestedFixDiff()).append("\n```");
            body.append("\n</details>");
        }
        
        // Add link to full issue details if available
        if (issue.getIssueUrl() != null && !issue.getIssueUrl().isBlank()) {
            body.append("\n\n[View Details](").append(issue.getIssueUrl()).append(")");
        }
        
        // Add CodeCrow marker for identification
        body.append("\n\n").append(CODECROW_INLINE_MARKER);
        
        return body.toString();
    }

    private void postReviewCheckSummary(GitLabClient client, VcsRepoInfo repository, Long prNumber, String markdown) {
        if (markdown == null) return;
        String marker = "<!-- codecrow-review-checks -->";
        String owner = repository.getRepoWorkspace(), repo = repository.getRepoSlug();
        try {
            Long id = client.findMergeRequestNoteByMarker(owner, repo, prNumber, marker);
            if (markdown.isBlank()) {
                if (id != null) client.deleteMergeRequestNote(owner, repo, prNumber, id);
            } else if (id != null) {
                client.updateMergeRequestNote(owner, repo, prNumber, id, marker + "\n" + markdown);
            } else {
                client.postMergeRequestComment(owner, repo, prNumber, marker + "\n" + markdown);
            }
        } catch (IOException unavailable) {
            log.warn("Could not publish review-check summary on GitLab MR {}: {}", prNumber, unavailable.getMessage());
        }
    }

    private void postOrUpdateComment(GitLabClient client, VcsRepoInfo repository, Long prNumber,
                                     String markdown, String placeholderCommentId) throws IOException {
        String owner = repository.getRepoWorkspace(), repo = repository.getRepoSlug();
        List<Long> existing;
        try {
            existing = client.listMergeRequestNotes(owner, repo, prNumber).stream()
                .filter(note -> !"DiffNote".equals(note.get("type")) && note.get("position") == null)
                .filter(note -> String.valueOf(note.get("body")).contains(CODECROW_COMMENT_MARKER))
                .map(note -> ((Number) note.get("id")).longValue()).distinct().toList();
        } catch (IOException unavailable) {
            if (placeholderCommentId == null) throw unavailable;
            log.warn("Could not list previous reports; updating the known placeholder: {}", unavailable.getMessage());
            existing = List.of();
        }
        Long keep = placeholderCommentId != null ? Long.valueOf(placeholderCommentId) : existing.stream().findFirst().orElse(null);
        String content = CODECROW_COMMENT_MARKER + "\n" + markdown;
        if (keep == null) client.postMergeRequestComment(owner, repo, prNumber, content);
        else client.updateMergeRequestNote(owner, repo, prNumber, keep, content);
        for (Long id : existing) {
            if (!id.equals(keep)) {
                try { client.deleteMergeRequestNote(owner, repo, prNumber, id); }
                catch (IOException failed) { log.warn("Could not remove superseded review comment {}: {}", id, failed.getMessage()); }
            }
        }
    }

    
    @Override
    public String postComment(
            Project project,
            Long mergeRequestIid,
            String content,
            String marker
    ) throws IOException {
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        GitLabClient client = getClient(vcsRepoInfo);
        
        // Add marker at the END as HTML comment (invisible to users) if provided
        String markedContent = content;
        if (marker != null && !marker.isBlank()) {
            markedContent = content + "\n\n" + marker;
        }
        
        // Post the comment
        client.postMergeRequestComment(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                mergeRequestIid,
                markedContent
        );
        
        // Find the comment we just posted to get its ID
        Long commentId = client.findMergeRequestNoteByMarker(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                mergeRequestIid,
                marker != null ? marker : markedContent.substring(0, Math.min(50, markedContent.length()))
        );
        
        return commentId != null ? commentId.toString() : null;
    }
    
    @Override
    public String postCommentReply(
            Project project,
            Long mergeRequestIid,
            String parentCommentId,
            String content
    ) throws IOException {
        // GitLab doesn't support direct comment replies on MR notes
        // Use basic postComment without context - caller should use postCommentReplyWithContext
        return postComment(project, mergeRequestIid, content, null);
    }
    
    @Override
    public String postCommentReplyWithContext(
            Project project,
            Long mergeRequestIid,
            String parentCommentId,
            boolean inlineComment,
            String content,
            String originalAuthorUsername,
            String originalCommentBody
    ) throws IOException {
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        GitLabClient client = getClient(vcsRepoInfo);
        if (parentCommentId != null && !parentCommentId.isBlank()
                && !parentCommentId.chars().allMatch(Character::isDigit)) {
            try {
                return client.postMergeRequestDiscussionReply(
                        vcsRepoInfo.getRepoWorkspace(),
                        vcsRepoInfo.getRepoSlug(),
                        mergeRequestIid,
                        parentCommentId,
                        content);
            } catch (IOException error) {
                log.warn("Failed to reply to GitLab discussion {}; using MR note fallback: {}",
                        parentCommentId, error.getMessage());
            }
        }

        // General MR notes do not have native threads. Format a fallback note
        // with a quote and mention to retain a visible connection.
        StringBuilder formattedReply = new StringBuilder();
        
        // Add mention of original author
        if (originalAuthorUsername != null && !originalAuthorUsername.isBlank()) {
            formattedReply.append("@").append(originalAuthorUsername).append(" ");
        }
        
        // Add quoted command (truncated to keep it clean)
        if (originalCommentBody != null && !originalCommentBody.isBlank()) {
            String truncatedQuestion = originalCommentBody.length() > 100 
                ? originalCommentBody.substring(0, 100) + "..." 
                : originalCommentBody;
            formattedReply.append("\n> ").append(truncatedQuestion.replace("\n", "\n> ")).append("\n\n");
        }
        
        formattedReply.append(content);
        
        return postComment(project, mergeRequestIid, formattedReply.toString(), null);
    }
    
    @Override
    public int deleteCommentsByMarker(
            Project project,
            Long mergeRequestIid,
            String marker
    ) throws IOException {
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        GitLabClient client = getClient(vcsRepoInfo);
        
        int deletedCount = 0;
        try {
            List<Map<String, Object>> notes = client.listMergeRequestNotes(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    mergeRequestIid
            );
            
            for (Map<String, Object> note : notes) {
                Object bodyObj = note.get("body");
                if (bodyObj != null && bodyObj.toString().contains(marker)) {
                    Object idObj = note.get("id");
                    if (idObj instanceof Number) {
                        long noteId = ((Number) idObj).longValue();
                        try {
                            client.deleteMergeRequestNote(
                                    vcsRepoInfo.getRepoWorkspace(),
                                    vcsRepoInfo.getRepoSlug(),
                                    mergeRequestIid,
                                    noteId
                            );
                            deletedCount++;
                        } catch (Exception e) {
                            log.warn("Failed to delete comment {}: {}", noteId, e.getMessage());
                        }
                    }
                }
            }
        } catch (Exception e) {
            log.warn("Failed to delete comments with marker {}: {}", marker, e.getMessage());
        }
        
        return deletedCount;
    }
    
    @Override
    public void deleteComment(
            Project project,
            Long mergeRequestIid,
            String commentId
    ) throws IOException {
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        GitLabClient client = getClient(vcsRepoInfo);
        client.deleteMergeRequestNote(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                mergeRequestIid,
                Long.parseLong(commentId)
        );
    }
    
    @Override
    public void updateComment(
            Project project,
            Long mergeRequestIid,
            String commentId,
            String newContent,
            String marker
    ) throws IOException {
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        GitLabClient client = getClient(vcsRepoInfo);
        
        // Add marker at the END as HTML comment (invisible to users) if provided
        String markedContent = newContent;
        if (marker != null && !marker.isBlank()) {
            markedContent = newContent + "\n\n" + marker;
        }
        
        client.updateMergeRequestNote(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                mergeRequestIid,
                Long.parseLong(commentId),
                markedContent
        );
    }
    
    @Override
    public boolean supportsMermaidDiagrams() {
        // GitLab supports Mermaid diagrams in markdown
        // TODO: Mermaid diagrams disabled for now - AI-generated Mermaid often has syntax errors
        // that fail to render. Using ASCII diagrams until we add validation/fixing.
        return false;
    }

    private GitLabClient getClient(VcsRepoInfo vcsRepoInfo) {
        return (GitLabClient) vcsClientProvider.getClient(vcsRepoInfo.getVcsConnection());
    }
}
