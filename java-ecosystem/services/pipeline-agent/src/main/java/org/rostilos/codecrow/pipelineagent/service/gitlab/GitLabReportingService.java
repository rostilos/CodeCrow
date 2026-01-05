package org.rostilos.codecrow.pipelineagent.service.gitlab;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.rostilos.codecrow.vcsclient.bitbucket.service.ReportGenerator;
import org.rostilos.codecrow.vcsclient.gitlab.actions.CommentOnMergeRequestAction;
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

        log.info("Posting analysis results to GitLab for MR {} (placeholderCommentId={})", 
            mergeRequestIid, placeholderCommentId);

        AnalysisSummary summary = reportGenerator.createAnalysisSummary(codeAnalysis, platformMrEntityId);
        // Use GitLab-specific markdown with collapsible sections for suggested fixes
        String markdownSummary = reportGenerator.createMarkdownSummary(codeAnalysis, summary, true);

        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);

        OkHttpClient httpClient = vcsClientProvider.getHttpClient(
                vcsRepoInfo.getVcsConnection()
        );

        // Post or update MR comment with detailed analysis
        postOrUpdateComment(httpClient, vcsRepoInfo, mergeRequestIid, markdownSummary, placeholderCommentId);

        log.info("Successfully posted analysis results to GitLab");
    }

    private void postOrUpdateComment(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            Long mergeRequestIid,
            String markdownSummary,
            String placeholderCommentId
    ) throws IOException {

        log.debug("Posting/updating summary comment to MR {} (placeholderCommentId={})", 
            mergeRequestIid, placeholderCommentId);

        CommentOnMergeRequestAction commentAction = new CommentOnMergeRequestAction(httpClient);
        
        if (placeholderCommentId != null) {
            // Update the placeholder comment with the analysis results
            String markedComment = CODECROW_COMMENT_MARKER + "\n" + markdownSummary;
            commentAction.updateNote(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    mergeRequestIid.intValue(),
                    Long.parseLong(placeholderCommentId),
                    markedComment
            );
            log.debug("Updated placeholder comment {} with analysis results", placeholderCommentId);
        } else {
            // Delete previous CodeCrow comments before posting new one
            try {
                deletePreviousComments(commentAction, vcsRepoInfo, mergeRequestIid.intValue());
                log.debug("Deleted previous CodeCrow comments from MR {}", mergeRequestIid);
            } catch (Exception e) {
                log.warn("Failed to delete previous comments: {}", e.getMessage());
            }
            
            // Add marker to the comment for future identification
            String markedComment = CODECROW_COMMENT_MARKER + "\n" + markdownSummary;
            
            commentAction.postComment(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    mergeRequestIid.intValue(),
                    markedComment
            );
        }
    }
    
    private void deletePreviousComments(
            CommentOnMergeRequestAction commentAction,
            VcsRepoInfo vcsRepoInfo,
            int mergeRequestIid
    ) throws IOException {
        List<Map<String, Object>> notes = commentAction.listNotes(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                mergeRequestIid
        );
        
        for (Map<String, Object> note : notes) {
            Object bodyObj = note.get("body");
            if (bodyObj != null && bodyObj.toString().contains(CODECROW_COMMENT_MARKER)) {
                Object idObj = note.get("id");
                if (idObj instanceof Number) {
                    long noteId = ((Number) idObj).longValue();
                    try {
                        commentAction.deleteNote(
                                vcsRepoInfo.getRepoWorkspace(),
                                vcsRepoInfo.getRepoSlug(),
                                mergeRequestIid,
                                noteId
                        );
                        log.debug("Deleted previous CodeCrow comment {}", noteId);
                    } catch (Exception e) {
                        log.warn("Failed to delete comment {}: {}", noteId, e.getMessage());
                    }
                }
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
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnMergeRequestAction commentAction = new CommentOnMergeRequestAction(httpClient);
        
        // Add marker at the END as HTML comment (invisible to users) if provided
        String markedContent = content;
        if (marker != null && !marker.isBlank()) {
            markedContent = content + "\n\n" + marker;
        }
        
        // Post the comment
        commentAction.postComment(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                mergeRequestIid.intValue(),
                markedContent
        );
        
        // Find the comment we just posted to get its ID
        Long commentId = commentAction.findCommentByMarker(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                mergeRequestIid.intValue(),
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
            String content,
            String originalAuthorUsername,
            String originalCommentBody
    ) throws IOException {
        // GitLab doesn't support threading on MR notes
        // Format reply with quote and @mention to create a visual connection
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
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnMergeRequestAction commentAction = new CommentOnMergeRequestAction(httpClient);
        
        int deletedCount = 0;
        try {
            List<Map<String, Object>> notes = commentAction.listNotes(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    mergeRequestIid.intValue()
            );
            
            for (Map<String, Object> note : notes) {
                Object bodyObj = note.get("body");
                if (bodyObj != null && bodyObj.toString().contains(marker)) {
                    Object idObj = note.get("id");
                    if (idObj instanceof Number) {
                        long noteId = ((Number) idObj).longValue();
                        try {
                            commentAction.deleteNote(
                                    vcsRepoInfo.getRepoWorkspace(),
                                    vcsRepoInfo.getRepoSlug(),
                                    mergeRequestIid.intValue(),
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
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnMergeRequestAction commentAction = new CommentOnMergeRequestAction(httpClient);
        commentAction.deleteNote(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                mergeRequestIid.intValue(),
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
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnMergeRequestAction commentAction = new CommentOnMergeRequestAction(httpClient);
        
        // Add marker at the END as HTML comment (invisible to users) if provided
        String markedContent = newContent;
        if (marker != null && !marker.isBlank()) {
            markedContent = newContent + "\n\n" + marker;
        }
        
        commentAction.updateNote(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                mergeRequestIid.intValue(),
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
}
