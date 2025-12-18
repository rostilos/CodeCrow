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
     * Gets VcsRepoInfo from project, trying ProjectVcsConnectionBinding first,
     * then falling back to VcsRepoBinding if not available.
     */
    private VcsRepoInfo getVcsRepoInfo(Project project) {
        // Try ProjectVcsConnectionBinding first (legacy path)
        if (project.getVcsBinding() != null) {
            return project.getVcsBinding();
        }

        // Fallback to VcsRepoBinding (APP-created projects)
        VcsRepoBinding vcsRepoBinding = vcsRepoBindingRepository.findByProject_Id(project.getId())
                .orElseThrow(() -> new IllegalStateException(
                        "No VCS binding found for project " + project.getId() +
                        ". Neither ProjectVcsConnectionBinding nor VcsRepoBinding is configured."
                ));

        log.debug("Using VcsRepoBinding fallback for project {}: {}/{}",
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

        log.info("Posting analysis results to Bitbucket for PR {}", pullRequestNumber);

        AnalysisSummary summary = reportGenerator.createAnalysisSummary(codeAnalysis, platformPrEntityId);
        String markdownSummary = reportGenerator.createMarkdownSummary(codeAnalysis, summary);
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

        postComment(httpClient, vcsRepoInfo, pullRequestNumber, markdownSummary);
        postReport(httpClient, vcsRepoInfo, codeAnalysis.getCommitHash(), report);
        postAnnotations(httpClient, vcsRepoInfo, codeAnalysis.getCommitHash(), annotations);

        log.info("Successfully posted analysis results to Bitbucket");
    }

    private void postComment(
            OkHttpClient httpClient,
            VcsRepoInfo vcsRepoInfo,
            Long pullRequestNumber,
            String markdownSummary
    ) throws IOException {

        log.debug("Posting summary comment to PR {}", pullRequestNumber);

        CommentOnBitbucketCloudAction commentAction = new CommentOnBitbucketCloudAction(
                httpClient,
                vcsRepoInfo,
                pullRequestNumber
        );

        commentAction.postSummaryResult(markdownSummary);
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
        
        // Include marker in comment if provided (for later identification/deletion)
        String fullContent = content;
        if (marker != null && !marker.isEmpty()) {
            fullContent = content + "\n\n<!-- codecrow-marker:" + marker + " -->";
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

        // TODO: Implement single comment deletion
        // Requires DELETE request to comments endpoint
        log.warn("Comment deletion not yet implemented for Bitbucket");
    }
    
    @Override
    public boolean supportsMermaidDiagrams() {
        // Bitbucket Cloud does not natively render Mermaid diagrams in comments
        return false;
    }
}