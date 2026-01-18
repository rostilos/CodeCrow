package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.CommentOnBitbucketCloudAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.PostReportOnBitbucketCloudAction;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsAnnotation;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsReport;
import org.rostilos.codecrow.vcsclient.bitbucket.service.ReportGenerator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.util.Set;

/**
 * Bitbucket-specific implementation of VcsReportingService.
 * Handles posting code analysis results to Bitbucket Cloud,
 * including comments and Code Insights annotations.
 */
@Service
public class BitbucketReportingService implements VcsReportingService {
    private static final Logger log = LoggerFactory.getLogger(BitbucketReportingService.class);
    
    private static final String CODECROW_REVIEW_MARKER = "<!-- codecrow-review -->";
    private static final String CODECROW_ISSUES_MARKER = "<!-- codecrow-issues -->";

    private final ReportGenerator reportGenerator;
    private final VcsClientProvider vcsClientProvider;
    private final VcsRepoBindingRepository vcsRepoBindingRepository;

    public BitbucketReportingService(
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
        return EVcsProvider.BITBUCKET_CLOUD;
    }

    /**
     * Gets VcsRepoInfo from project using the unified accessor.
     */
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

    /**
     * Posts complete analysis results to Bitbucket including summary comment,
     * Code Insights report, and annotations.
     *
     * @param codeAnalysis The code analysis results
     * @param project The project entity
     * @param pullRequestNumber The PR number on Bitbucket
     * @param platformPrEntityId The internal PR entity ID
     */
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
    
    /**
     * Posts complete analysis results to the Bitbucket platform.
     * If placeholderCommentId is provided, updates that comment instead of posting new.
     *
     * @param codeAnalysis The code analysis results
     * @param project The project entity
     * @param pullRequestNumber The PR number on Bitbucket
     * @param platformPrEntityId The internal PR entity ID
     * @param placeholderCommentId Optional ID of placeholder comment to update
     */
    @Override
    @Transactional(readOnly = true)
    public void postAnalysisResults(
            CodeAnalysis codeAnalysis,
            Project project,
            Long pullRequestNumber,
            Long platformPrEntityId,
            String placeholderCommentId
    ) throws IOException {

        log.info("Posting analysis results to Bitbucket for PR {} (placeholderCommentId={})", 
            pullRequestNumber, placeholderCommentId);

        AnalysisSummary summary = reportGenerator.createAnalysisSummary(codeAnalysis, platformPrEntityId);
        String markdownSummary = reportGenerator.createMarkdownSummary(codeAnalysis, summary);
        String detailedIssuesMarkdown = reportGenerator.createDetailedIssuesMarkdown(summary, false);
        CodeInsightsReport report = reportGenerator.createCodeInsightsReport(
                summary,
                codeAnalysis
        );
        Set<CodeInsightsAnnotation> annotations = reportGenerator.createReportAnnotations(
                codeAnalysis,
                project
        );

        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);

        OkHttpClient httpClient = vcsClientProvider.getHttpClient(
                vcsRepoInfo.getVcsConnection()
        );

        // Post summary comment (or update placeholder)
        String summaryCommentId = postOrUpdateComment(httpClient, vcsRepoInfo, pullRequestNumber, markdownSummary, placeholderCommentId);
        
        // Post detailed issues as a separate comment reply if there are issues
        if (detailedIssuesMarkdown != null && !detailedIssuesMarkdown.isEmpty() && summaryCommentId != null) {
            postDetailedIssuesReply(httpClient, vcsRepoInfo, pullRequestNumber, summaryCommentId, detailedIssuesMarkdown);
        }
        
        postReport(httpClient, vcsRepoInfo, codeAnalysis.getCommitHash(), report);
        postAnnotations(httpClient, vcsRepoInfo, codeAnalysis.getCommitHash(), annotations);

        log.info("Successfully posted analysis results to Bitbucket");
    }
    
    private String postOrUpdateComment(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            Long pullRequestNumber,
            String markdownSummary,
            String placeholderCommentId
    ) throws IOException {

        log.debug("Posting/updating summary comment to PR {} (placeholderCommentId={})", 
            pullRequestNumber, placeholderCommentId);

        CommentOnBitbucketCloudAction commentAction = new CommentOnBitbucketCloudAction(
                httpClient,
                vcsRepoInfo,
                pullRequestNumber
        );

        if (placeholderCommentId != null) {
            // Update the placeholder comment with the analysis results
            String fullContent = markdownSummary + "\n\n" + CODECROW_REVIEW_MARKER;
            commentAction.updateComment(placeholderCommentId, fullContent);
            log.debug("Updated placeholder comment {} with analysis results", placeholderCommentId);
            return placeholderCommentId;
        } else {
            return commentAction.postSummaryResultWithId(markdownSummary);
        }
    }
    
    private void postDetailedIssuesReply(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            Long pullRequestNumber,
            String parentCommentId,
            String detailedIssuesMarkdown
    ) throws IOException {
        try {
            log.debug("Posting detailed issues as reply to comment {} on PR {}", parentCommentId, pullRequestNumber);
            
            CommentOnBitbucketCloudAction commentAction = new CommentOnBitbucketCloudAction(
                    httpClient,
                    vcsRepoInfo,
                    pullRequestNumber
            );
            
            String content = detailedIssuesMarkdown + "\n\n" + CODECROW_ISSUES_MARKER;
            commentAction.postCommentReply(parentCommentId, content);
            
            log.debug("Posted detailed issues reply to PR {}", pullRequestNumber);
        } catch (Exception e) {
            log.warn("Failed to post detailed issues as reply, will be included in annotations: {}", e.getMessage());
        }
    }

    private void postReport(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            String commitHash,
            CodeInsightsReport report
    ) throws IOException {

        log.debug("Posting Code Insights report for commit {}", commitHash);

        PostReportOnBitbucketCloudAction reportAction = new PostReportOnBitbucketCloudAction(
                vcsRepoInfo,
                httpClient
        );

        reportAction.uploadReport(commitHash, report);
    }

    private void postAnnotations(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            String commitHash,
            Set<CodeInsightsAnnotation> annotations
    ) throws IOException {

        log.debug("Posting {} annotations for commit {}", annotations.size(), commitHash);

        PostReportOnBitbucketCloudAction reportAction = new PostReportOnBitbucketCloudAction(
                vcsRepoInfo,
                httpClient
        );

        reportAction.uploadAnnotations(commitHash, annotations);
    }
    
    // ==================== Comment Command Support Methods ====================
    
    @Override
    public String postComment(Project project, Long pullRequestNumber, String content, String marker) throws IOException {
        log.info("Posting comment to Bitbucket PR {}", pullRequestNumber);
        
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnBitbucketCloudAction commentAction = new CommentOnBitbucketCloudAction(
                httpClient,
                vcsRepoInfo,
                pullRequestNumber
        );
        
        // Add marker at the END as HTML comment (invisible to users) if provided
        // The marker itself is already an HTML comment, so just append it
        String fullContent = content;
        if (marker != null && !marker.isEmpty()) {
            fullContent = content + "\n\n" + marker;
        }
        
        // Use postSimpleComment - does NOT delete old comments or add summarize marker
        return commentAction.postSimpleComment(fullContent);
    }
    
    @Override
    public String postCommentReply(Project project, Long pullRequestNumber, String parentCommentId, String content) throws IOException {
        log.info("Posting comment reply to Bitbucket PR {} (parent: {})", pullRequestNumber, parentCommentId);
        
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnBitbucketCloudAction commentAction = new CommentOnBitbucketCloudAction(
                httpClient,
                vcsRepoInfo,
                pullRequestNumber
        );
        
        // Use the new postCommentReply method that properly creates a threaded reply
        return commentAction.postCommentReply(parentCommentId, content);
    }
    
    @Override
    public int deleteCommentsByMarker(Project project, Long pullRequestNumber, String marker) throws IOException {
        log.info("Deleting comments with marker {} from Bitbucket PR {}", marker, pullRequestNumber);
        
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnBitbucketCloudAction commentAction = new CommentOnBitbucketCloudAction(
                httpClient,
                vcsRepoInfo,
                pullRequestNumber
        );
        
        return commentAction.deleteCommentsByMarker(marker);
    }

    @Override
    public void deleteComment(Project project, Long pullRequestNumber, String commentId) throws IOException {
        log.info("Deleting comment {} from Bitbucket PR {}", commentId, pullRequestNumber);

        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnBitbucketCloudAction commentAction = new CommentOnBitbucketCloudAction(
                httpClient,
                vcsRepoInfo,
                pullRequestNumber
        );
        
        commentAction.deleteCommentById(commentId);
    }
    
    @Override
    public void updateComment(Project project, Long pullRequestNumber, String commentId, String newContent, String marker) throws IOException {
        log.info("Updating comment {} on Bitbucket PR {}", commentId, pullRequestNumber);
        
        VcsRepoInfo vcsRepoInfo = getVcsRepoInfo(project);
        OkHttpClient httpClient = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
        
        CommentOnBitbucketCloudAction commentAction = new CommentOnBitbucketCloudAction(
                httpClient,
                vcsRepoInfo,
                pullRequestNumber
        );
        
        // Add marker at the END as HTML comment (invisible to users) if provided
        String fullContent = newContent;
        if (marker != null && !marker.isEmpty()) {
            fullContent = newContent + "\n\n" + marker;
        }
        
        commentAction.updateComment(commentId, fullContent);
    }
    
    @Override
    public boolean supportsMermaidDiagrams() {
        // Bitbucket Cloud does not natively render Mermaid diagrams in comments
        return false;
    }
}