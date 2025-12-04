package org.rostilos.codecrow.pipelineagent.bitbucket.service;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.persistence.repository.vcs.VcsRepoBindingRepository;
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
 * Service for posting code analysis results to Bitbucket.
 * Handles report generation, comments, and Code Insights annotations.
 */
@Service
public class BitbucketReportingService {
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
}