package org.rostilos.codecrow.pipelineagent.bitbucket.processor.analysis;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.pipelineagent.bitbucket.service.BitbucketReportingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.Optional;

public abstract class AbstractAnalysisProcessor {
    private static final Logger log = LoggerFactory.getLogger(AbstractAnalysisProcessor.class);

    protected final CodeAnalysisService codeAnalysisService;
    protected final BitbucketReportingService reportingService;

    protected AbstractAnalysisProcessor(
            CodeAnalysisService codeAnalysisService,
            BitbucketReportingService reportingService
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.reportingService = reportingService;
    }

    protected boolean postAnalysisCacheIfExist(Project project, PullRequest pullRequest, String commitHash, Long prId) {
        Optional<CodeAnalysis> cachedAnalysis = codeAnalysisService.getCodeAnalysisCache(
                project.getId(),
                commitHash,
                prId
        );

        if (cachedAnalysis.isPresent()) {
            try {
                reportingService.postAnalysisResults(
                        cachedAnalysis.get(),
                        project,
                        prId,
                        pullRequest.getId()
                );
            } catch (IOException e) {
                log.error("Failed to post cached analysis results to Bitbucket: {}", e.getMessage(), e);
                // Don't fail the whole request just because posting failed
                // The analysis is already cached and can be retrieved
            }
            return true;
        }
        return false;
    }
}
