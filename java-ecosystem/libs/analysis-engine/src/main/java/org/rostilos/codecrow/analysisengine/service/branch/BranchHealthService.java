package org.rostilos.codecrow.analysisengine.service.branch;

import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.scmevidence.service.ScmEvidenceService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class BranchHealthService {
    private static final Logger log = LoggerFactory.getLogger(BranchHealthService.class);

    private final BranchRepository branchRepository;
    private final AnalyzedCommitService analyzedCommitService;

    @Autowired(required = false)
    private ScmEvidenceService scmEvidenceService;

    public BranchHealthService(
            BranchRepository branchRepository,
            AnalyzedCommitService analyzedCommitService
    ) {
        this.branchRepository = branchRepository;
        this.analyzedCommitService = analyzedCommitService;
    }

    public void markBranchHealthy(Project project, BranchProcessRequest request) {
        branchRepository.findByProjectIdAndBranchName(project.getId(), request.getTargetBranchName())
                .ifPresent(b -> {
                    b.markHealthy(request.getCommitHash());
                    branchRepository.save(b);
                });
    }

    public void recordCommitsAnalyzed(Project project, List<String> unanalyzedCommits,
                                       String branchName) {
        if (unanalyzedCommits.isEmpty()) return;
        try {
            boolean multiBranch = project.getConfiguration() != null
                    && project.getConfiguration().ragConfig() != null
                    && project.getConfiguration().ragConfig().isMultiBranchEnabled();
            if (multiBranch) {
                analyzedCommitService.recordBranchCommitsAnalyzed(
                        project, unanalyzedCommits, branchName);
            } else {
                analyzedCommitService.recordBranchCommitsAnalyzed(
                        project, unanalyzedCommits);
            }
            if (scmEvidenceService != null) {
                scmEvidenceService.recordAnalysisReceipts(
                        project.getId(), unanalyzedCommits, branchName, branchName,
                        unanalyzedCommits.get(unanalyzedCommits.size() - 1),
                        null, AnalysisType.BRANCH_ANALYSIS.name());
            }
            log.info("Recorded {} commits as analyzed after successful branch analysis (branch={})",
                    unanalyzedCommits.size(), branchName);
        } catch (Exception e) {
            log.warn("Failed to record commits as analyzed (non-critical): {}", e.getMessage());
        }
    }

    public void handleProcessFailure(Project project, BranchProcessRequest request,
                                     List<String> unanalyzedCommits, Exception e) {
        try {
            branchRepository.findByProjectIdAndBranchName(project.getId(), request.getTargetBranchName())
                    .ifPresent(b -> {
                        b.markStale();
                        branchRepository.save(b);
                        log.info("Marked branch {} as STALE (consecutiveFailures={})",
                                request.getTargetBranchName(), b.getConsecutiveFailures());
                    });
        } catch (Exception staleEx) {
            log.warn("Failed to mark branch as STALE: {}", staleEx.getMessage());
        }

        // Failed commits are simply NOT recorded in analyzed_commit —
        // they will be picked up on the next push automatically.

        log.warn("Branch reconciliation failed (Branch: {}, Commit: {}): {}",
                request.getTargetBranchName(), request.getCommitHash(), e.getMessage());
    }
}
