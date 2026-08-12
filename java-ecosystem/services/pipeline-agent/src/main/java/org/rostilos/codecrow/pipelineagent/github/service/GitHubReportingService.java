package org.rostilos.codecrow.pipelineagent.github.service;

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
import org.rostilos.codecrow.vcsclient.github.actions.GetPullRequestDiffAction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.util.List;
import java.util.Map;

/**
 * GitHub implementation of VcsReportingService.
 * Posts analysis results as PR comments and Check Runs.
 */
@Service
public class GitHubReportingService implements VcsReportingService {
    private static final Logger log = LoggerFactory.getLogger(GitHubReportingService.class);
    
    /**
     * Marker text used to identify CodeCrow comments for deletion.
     */
    private static final String CODECROW_COMMENT_MARKER = "<!-- codecrow-analysis-comment -->";
    private static final String CODECROW_REVIEW_MARKER = "<!-- codecrow-analysis-review -->";
    private static final String CODECROW_CLEARED_REVIEW_MARKER =
            "<!-- codecrow-analysis-review-cleared -->";

    private final ReportGenerator reportGenerator;
    private final VcsClientProvider vcsClientProvider;
    private final VcsRepoBindingRepository vcsRepoBindingRepository;
    private final GitHubReviewFormatter reviewFormatter = new GitHubReviewFormatter();

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
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(
                vcsRepoInfo.getVcsConnection()
        );
        GitHubReviewFormatter.ReviewPlan reviewPlan = createReviewPlan(
                httpClient, vcsRepoInfo, pullRequestNumber, summary);

        // Use GitHub-specific markdown with collapsible spoilers for suggested fixes
        String markdownSummary = reportGenerator.createMarkdownSummary(codeAnalysis, summary, true);
        String nonInlineFindingsMarkdown = reviewFormatter.formatNonInlineFindings(
                reviewPlan.nonInlineFindings());
        String detailedIssuesMarkdown = reportGenerator.createDetailedIssuesMarkdown(summary, true);
        
        // GitHub doesn't support threaded replies for issue comments like Bitbucket does.
        // So we combine summary and detailed issues into ONE comment.
        String fullComment = markdownSummary;
        if (!nonInlineFindingsMarkdown.isEmpty()) {
            fullComment += "\n\n---\n\n" + nonInlineFindingsMarkdown;
        }
        if (detailedIssuesMarkdown != null && !detailedIssuesMarkdown.isEmpty()) {
            fullComment += "\n\n---\n\n" + detailedIssuesMarkdown;
        }

        // Post or update comment with full content (summary + issues)
        if (placeholderCommentId != null) {
            updatePlaceholderComment(httpClient, vcsRepoInfo, pullRequestNumber, fullComment, placeholderCommentId);
        } else {
            postSummaryComment(httpClient, vcsRepoInfo, pullRequestNumber, fullComment);
        }

        // Publish findings as a submitted COMMENT review so they appear inline in
        // "Files changed" and as grouped reviewer comments in the PR conversation.
        postInlineReviewComments(
                httpClient, vcsRepoInfo, pullRequestNumber, codeAnalysis, reviewPlan);
        
        // Create Check Run for the commit
        createCheckRun(httpClient, vcsRepoInfo, codeAnalysis, summary);

        log.info("Successfully posted analysis results to GitHub");
    }

    private GitHubReviewFormatter.ReviewPlan createReviewPlan(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            Long pullRequestNumber,
            AnalysisSummary summary
    ) {
        try {
            List<GetPullRequestDiffAction.PullRequestFilePatch> filePatches =
                    new GetPullRequestDiffAction(httpClient).getPullRequestFilePatches(
                            vcsRepoInfo.getRepoWorkspace(),
                            vcsRepoInfo.getRepoSlug(),
                            pullRequestNumber.intValue());
            GitHubReviewFormatter.ReviewPlan plan = reviewFormatter.planComments(
                    summary.getIssues(), CODECROW_REVIEW_MARKER, filePatches);
            log.info("Prepared GitHub review plan for PR {}: {} inline, {} not inline",
                    pullRequestNumber,
                    plan.inlineComments().size(),
                    plan.nonInlineFindings().size());
            return plan;
        } catch (Exception e) {
            // Diff enrichment is supplemental. Publishing an unverified anchor can
            // make GitHub reject the whole review, so retain every finding in the
            // aggregate comment instead of guessing.
            log.warn("Could not load GitHub PR diff for inline comment planning on PR {}: {}. "
                            + "Findings will remain in the aggregate comment.",
                    pullRequestNumber, e.getMessage());
            return reviewFormatter.planWithoutDiff(summary.getIssues());
        }
    }
    
    /**
     * Post summary as a regular comment.
     */
    private void postSummaryComment(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            Long pullRequestNumber,
            String summaryMarkdown
    ) throws IOException {
        CommentOnPullRequestAction commentAction = new CommentOnPullRequestAction(httpClient);
        
        // Delete previous CodeCrow summary comments first
        try {
            commentAction.deletePreviousComments(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    pullRequestNumber.intValue(),
                    CODECROW_COMMENT_MARKER
            );
        } catch (Exception e) {
            log.warn("Failed to delete previous summary comments: {}", e.getMessage());
        }
        
        String markedBody = CODECROW_COMMENT_MARKER + "\n" + summaryMarkdown;
        
        commentAction.postComment(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                pullRequestNumber.intValue(),
                markedBody
        );
        
        log.debug("Posted summary comment to PR {}", pullRequestNumber);
    }
    
    /**
     * Update placeholder comment with summary.
     */
    private void updatePlaceholderComment(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            Long pullRequestNumber,
            String summaryMarkdown,
            String placeholderCommentId
    ) throws IOException {
        CommentOnPullRequestAction commentAction = new CommentOnPullRequestAction(httpClient);
        
        String markedBody = CODECROW_COMMENT_MARKER + "\n" + summaryMarkdown;
        
        commentAction.updateComment(
                vcsRepoInfo.getRepoWorkspace(),
                vcsRepoInfo.getRepoSlug(),
                Long.parseLong(placeholderCommentId),
                markedBody
        );
        
        log.debug("Updated placeholder comment {} with summary", placeholderCommentId);
    }

    private void postInlineReviewComments(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            Long pullRequestNumber,
            CodeAnalysis codeAnalysis,
            GitHubReviewFormatter.ReviewPlan reviewPlan
    ) {
        CommentOnPullRequestAction commentAction = new CommentOnPullRequestAction(httpClient);
        List<Map<String, Object>> comments = reviewPlan.inlineComments();
        if (comments.isEmpty()) {
            cleanupPreviousInlineReviewComments(
                    commentAction, vcsRepoInfo, pullRequestNumber, null);
            log.debug("No diff-verified issues to post as GitHub review comments");
            return;
        }

        String commitHash = codeAnalysis.getCommitHash();
        if (commitHash == null || commitHash.isBlank()) {
            log.warn("Cannot post GitHub review comments for PR {}: commit hash is missing",
                    pullRequestNumber);
            return;
        }

        try {
            String reviewId = commentAction.createPullRequestReview(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    pullRequestNumber.intValue(),
                    commitHash,
                    reviewFormatter.formatReviewBody(comments.size(), CODECROW_REVIEW_MARKER),
                    "COMMENT",
                    comments
            );
            log.info("Posted GitHub review with {} inline comment(s) on PR {}",
                    comments.size(), pullRequestNumber);
            Long preservedReviewId = parseReviewId(reviewId);
            if (preservedReviewId == null) {
                log.warn("GitHub created the replacement review for PR {} without a usable ID; "
                                + "leaving previous review artifacts intact",
                        pullRequestNumber);
            } else {
                cleanupPreviousInlineReviewComments(
                        commentAction,
                        vcsRepoInfo,
                        pullRequestNumber,
                        preservedReviewId);
            }
        } catch (Exception e) {
            // Inline review publishing is supplemental. The aggregate comment
            // still contains the complete result. Cleanup deliberately happens
            // only after a replacement succeeds, so a rejected review does not
            // erase the last successfully published inline comments.
            log.warn("Failed to post inline GitHub review comments on PR {}: {}. "
                            + "Issues remain available in the summary comment.",
                    pullRequestNumber, e.getMessage());
        }
    }

    private Long parseReviewId(String reviewId) {
        if (reviewId == null || reviewId.isBlank()) {
            return null;
        }
        try {
            return Long.parseLong(reviewId);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private void cleanupPreviousInlineReviewComments(
            CommentOnPullRequestAction commentAction,
            VcsRepoInfo vcsRepoInfo,
            Long pullRequestNumber,
            Long preservedReviewId
    ) {
        try {
            int deleted = commentAction.deletePreviousReviewComments(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    pullRequestNumber.intValue(),
                    CODECROW_REVIEW_MARKER,
                    preservedReviewId
            );
            if (deleted > 0) {
                log.info("Deleted {} previous CodeCrow inline review comment(s) from PR {}",
                        deleted, pullRequestNumber);
            }
        } catch (Exception e) {
            // Cleanup is best effort. A temporary list/delete failure must not hide
            // the current analysis or block Check Run publication.
            log.warn("Failed to delete previous CodeCrow inline review comments from PR {}: {}",
                    pullRequestNumber, e.getMessage());
        }

        try {
            int cleared = commentAction.clearPreviousReviewBodies(
                    vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(),
                    pullRequestNumber.intValue(),
                    CODECROW_REVIEW_MARKER,
                    CODECROW_CLEARED_REVIEW_MARKER,
                    preservedReviewId
            );
            if (cleared > 0) {
                log.info("Cleared {} previous CodeCrow review summary body/bodies from PR {}",
                        cleared, pullRequestNumber);
            }
        } catch (Exception e) {
            // A submitted GitHub review cannot be deleted. Clearing its generated
            // summary is independent from inline-comment cleanup and remains
            // best effort so current result publication can continue.
            log.warn("Failed to clear previous CodeCrow review summaries from PR {}: {}",
                    pullRequestNumber, e.getMessage());
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
            boolean inlineComment,
            String content,
            String originalAuthorUsername,
            String originalCommentBody
    ) throws IOException {
        if (inlineComment) {
            VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
            OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
            CommentOnPullRequestAction commentAction = new CommentOnPullRequestAction(httpClient);
            try {
                return commentAction.postReviewCommentReply(
                        vcsRepoInfo.getRepoWorkspace(),
                        vcsRepoInfo.getRepoSlug(),
                        pullRequestNumber.intValue(),
                        Long.parseLong(parentCommentId),
                        content);
            } catch (NumberFormatException | IOException error) {
                log.debug("Comment {} is not a GitHub review-thread root; using timeline reply: {}",
                        parentCommentId, error.getMessage());
            }
        }

        // Issue comments do not have native threads. Format a timeline reply
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
