package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.HttpAuthorizedClientFactory;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.CommentOnBitbucketCloudAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.PostReportOnBitbucketCloudAction;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.AnalysisSummary;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsAnnotation;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsReport;
import org.rostilos.codecrow.vcsclient.bitbucket.service.ReportGenerator;
import org.rostilos.codecrow.vcsclient.bitbucket.service.VcsConnectionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.util.Set;

/**
 * Service for posting code analysis results to Bitbucket.
 * Handles report generation, comments, and Code Insights annotations.
 */
@Service
public class BitbucketReportingService {
    private static final Logger log = LoggerFactory.getLogger(BitbucketReportingService.class);

    private final ReportGenerator reportGenerator;
    private final VcsConnectionService vcsConnectionService;

    public BitbucketReportingService(
            ReportGenerator reportGenerator,
            VcsConnectionService vcsConnectionService
    ) {
        this.reportGenerator = reportGenerator;
        this.vcsConnectionService = vcsConnectionService;
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

        OkHttpClient httpClient = vcsConnectionService.getBitbucketAuthorizedClient(
                project.getWorkspace().getId(),
                project.getVcsBinding().getVcsConnection().getId()
        );

        postComment(httpClient, project, pullRequestNumber, markdownSummary);
        postReport(httpClient, project, codeAnalysis.getCommitHash(), report);
        postAnnotations(httpClient, project, codeAnalysis.getCommitHash(), annotations);

        log.info("Successfully posted analysis results to Bitbucket");
    }

    private void postComment(
            OkHttpClient httpClient,
            Project project,
            Long pullRequestNumber,
            String markdownSummary
    ) throws IOException {

        log.debug("Posting summary comment to PR {}", pullRequestNumber);

        CommentOnBitbucketCloudAction commentAction = new CommentOnBitbucketCloudAction(
                httpClient,
                project.getVcsBinding(),
                pullRequestNumber
        );

        commentAction.postSummaryResult(markdownSummary);
    }

    private void postReport(
            OkHttpClient httpClient,
            Project project,
            String commitHash,
            CodeInsightsReport report
    ) throws IOException {

        log.debug("Posting Code Insights report for commit {}", commitHash);

        PostReportOnBitbucketCloudAction reportAction = new PostReportOnBitbucketCloudAction(
                project.getVcsBinding(),
                httpClient
        );

        reportAction.uploadReport(commitHash, report);
    }

    private void postAnnotations(
            OkHttpClient httpClient,
            Project project,
            String commitHash,
            Set<CodeInsightsAnnotation> annotations
    ) throws IOException {

        log.debug("Posting {} annotations for commit {}", annotations.size(), commitHash);

        PostReportOnBitbucketCloudAction reportAction = new PostReportOnBitbucketCloudAction(
                project.getVcsBinding(),
                httpClient
        );

        reportAction.uploadAnnotations(commitHash, annotations);
    }
}