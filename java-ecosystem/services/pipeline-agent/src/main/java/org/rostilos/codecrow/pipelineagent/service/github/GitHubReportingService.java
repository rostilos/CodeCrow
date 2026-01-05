package org.rostilos.codecrow.pipelineagent.service.github;

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
import org.rostilos.codecrow.vcsclient.github.actions.CheckRunAction;
import org.rostilos.codecrow.vcsclient.github.actions.CommentOnPullRequestAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;

/**
 * GitHub implementation of VcsReportingService.
 * Posts analysis results as PR comments and Check Runs.
 */
@Service
public class GitHubReportingService implements VcsReportingService {
    private static final Logger log = LoggerFactory.getLogger(GitHubReportingService.class);
    
    /**
     * Marker text used to identify CodeCrow comments for deletion.
     * This should be unique enough to not match user comments.
     */
    private static final String CODECROW_COMMENT_MARKER = "<!-- codecrow-analysis-comment -->";

    private final ReportGenerator reportGenerator;
    private final VcsClientProvider vcsClientProvider;
    private final VcsRepoBindingRepository vcsRepoBindingRepository;

    public GitHubReportingService(
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
        return EVcsProvider.GITHUB;
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
            Long pullRequestNumber,
            Long platformPrEntityId
    ) throws IOException {
        postAnalysisResults(codeAnalysis, project, pullRequestNumber, platformPrEntityId, null);
    }
    
    @Override
    @Transactional(readOnly = true)
    public void postAnalysisResults(
            CodeAnalysis codeAnalysis,
            Project project,
            Long pullRequestNumber,
            Long platformPrEntityId,
            String placeholderCommentId
    ) throws IOException {

        log.info("Posting analysis results to GitHub for PR {} (placeholderCommentId={})", 
            pullRequestNumber, placeholderCommentId);

        AnalysisSummary summary = reportGenerator.createAnalysisSummary(codeAnalysis, platformPrEntityId);
        // Use GitHub-specific markdown with collapsible spoilers for suggested fixes
        String markdownSummary = reportGenerator.createMarkdownSummary(codeAnalysis, summary, true);

        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);

        OkHttpClient httpClient = vcsClientProvider.getHttpClient(
                vcsRepoInfo.getVcsConnection()
        );

        // Post or update PR comment with detailed analysis
        postOrUpdateComment(httpClient, vcsRepoInfo, pullRequestNumber, markdownSummary, placeholderCommentId);
        
        // Create Check Run for the commit
        createCheckRun(httpClient, vcsRepoInfo, codeAnalysis, summary);

        log.info("Successfully posted analysis results to GitHub");
    }

    private void postOrUpdateComment(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            Long pullRequestNumber,
            String markdownSummary,
            String placeholderCommentId
    ) throws IOException {

        log.debug("Posting/updating summary comment to PR {} (placeholderCommentId={})", 
            pullRequestNumber, placeholderCommentId);

        CommentOnPullRequestAction commentAction = new CommentOnPullRequestAction(httpClient);
        
        if (placeholderCommentId != null) {
            // Update the placeholder comment with the analysis results
            String markedComment = CODECROW_COMMENT_MARKER + "\n" + markdownSummary;
            commentAction.updateComment(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    Long.parseLong(placeholderCommentId),
                    markedComment
            );
            log.debug("Updated placeholder comment {} with analysis results", placeholderCommentId);
        } else {
            // Delete previous CodeCrow comments before posting new one
            try {
                commentAction.deletePreviousComments(
                        vcsRepoInfo.getRepoWorkspace(),
                        vcsRepoInfo.getRepoSlug(),
                        pullRequestNumber.intValue(),
                        CODECROW_COMMENT_MARKER
                );
                log.debug("Deleted previous CodeCrow comments from PR {}", pullRequestNumber);
            } catch (Exception e) {
                log.warn("Failed to delete previous comments: {}", e.getMessage());
            }
            
            // Add marker to the comment for future identification
            String markedComment = CODECROW_COMMENT_MARKER + "\n" + markdownSummary;
            
            commentAction.postComment(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    pullRequestNumber.intValue(),
                    markedComment
            );
        }
    }
    
    private void createCheckRun(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            CodeAnalysis codeAnalysis,
            AnalysisSummary summary
    ) {
        try {
            log.debug("Creating Check Run for commit {}", codeAnalysis.getCommitHash());
            
            CheckRunAction checkRunAction = new CheckRunAction(httpClient);
            checkRunAction.createCheckRun(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    codeAnalysis.getCommitHash(),
                    summary
            );
            
            log.info("Successfully created Check Run for commit {}", codeAnalysis.getCommitHash());
        } catch (Exception e) {
            // Don't fail the whole operation if Check Run creation fails
            log.warn("Failed to create Check Run for commit {}: {}", 
                    codeAnalysis.getCommitHash(), e.getMessage());
        }
    }
    
    @Override
    public String postComment(
            Project project,
            Long pullRequestNumber,
            String content,
            String marker
    ) throws IOException {
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnPullRequestAction commentAction = new CommentOnPullRequestAction(httpClient);
        
        // Add marker at the END as HTML comment (invisible to users) if provided
        String markedContent = content;
        if (marker != null && !marker.isBlank()) {
            markedContent = content + "\n\n" + marker;
        }
        
        // Use postCommentWithId to get the comment ID back
        return commentAction.postCommentWithId(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                pullRequestNumber.intValue(),
                markedContent
        );
    }
    
    @Override
    public String postCommentReply(
            Project project,
            Long pullRequestNumber,
            String parentCommentId,
            String content
    ) throws IOException {
        // GitHub doesn't support direct comment replies on issue comments
        // Use basic postComment without context - caller should use postCommentReplyWithContext
        return postComment(project, pullRequestNumber, content, null);
    }
    
    @Override
    public String postCommentReplyWithContext(
            Project project,
            Long pullRequestNumber,
            String parentCommentId,
            String content,
            String originalAuthorUsername,
            String originalCommentBody
    ) throws IOException {
        // GitHub doesn't support threading on issue comments
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
        
        return postComment(project, pullRequestNumber, formattedReply.toString(), null);
    }
    
    @Override
    public int deleteCommentsByMarker(
            Project project,
            Long pullRequestNumber,
            String marker
    ) throws IOException {
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnPullRequestAction commentAction = new CommentOnPullRequestAction(httpClient);
        
        try {
            commentAction.deletePreviousComments(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    pullRequestNumber.intValue(),
                    marker
            );
            return 1; // Approximate count
        } catch (Exception e) {
            log.warn("Failed to delete comments with marker {}: {}", marker, e.getMessage());
            return 0;
        }
    }
    
    @Override
    public void deleteComment(
            Project project,
            Long pullRequestNumber,
            String commentId
    ) throws IOException {
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnPullRequestAction commentAction = new CommentOnPullRequestAction(httpClient);
        commentAction.deleteComment(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                Long.parseLong(commentId)
        );
    }
    
    @Override
    public void updateComment(
            Project project,
            Long pullRequestNumber,
            String commentId,
            String newContent,
            String marker
    ) throws IOException {
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnPullRequestAction commentAction = new CommentOnPullRequestAction(httpClient);
        
        // Add marker at the END as HTML comment (invisible to users) if provided
        String markedContent = newContent;
        if (marker != null && !marker.isBlank()) {
            markedContent = newContent + "\n\n" + marker;
        }
        
        commentAction.updateComment(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                Long.parseLong(commentId),
                markedContent
        );
    }
    
    @Override
    public boolean supportsMermaidDiagrams() {
        // TODO: Mermaid diagrams disabled for now - AI-generated Mermaid often has syntax errors
        // that fail to render on GitHub. Using ASCII diagrams until we add validation/fixing.
        // Original: return true; (GitHub fully supports Mermaid diagrams in markdown)
        return false;
    }
}
