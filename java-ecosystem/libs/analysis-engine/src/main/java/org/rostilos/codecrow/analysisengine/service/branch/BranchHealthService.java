package org.rostilos.codecrow.analysisengine.service.branch;

import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.service.gitgraph.GitGraphSyncService;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class BranchHealthService {
    private static final Logger log = LoggerFactory.getLogger(BranchHealthService.class);

    private final BranchRepository branchRepository;
    private final GitGraphSyncService gitGraphSyncService;

    public BranchHealthService(
            BranchRepository branchRepository,
            GitGraphSyncService gitGraphSyncService
    ) {
        this.branchRepository = branchRepository;
        this.gitGraphSyncService = gitGraphSyncService;
    }

    public void markBranchHealthy(Project project, BranchProcessRequest request) {
        branchRepository.findByProjectIdAndBranchName(project.getId(), request.getTargetBranchName())
                .ifPresent(b -> {
                    b.markHealthy(request.getCommitHash());
                    branchRepository.save(b);
                });
    }

    public void markDagCommitsAnalyzed(Project project, List<String> unanalyzedCommits,
                                       String branchName) {
        if (unanalyzedCommits.isEmpty()) return;
        try {
            gitGraphSyncService.markCommitsAnalyzed(project.getId(), unanalyzedCommits);
            log.info("DAG: Marked {} commits as ANALYZED after successful branch analysis (branch={})",
                    unanalyzedCommits.size(), branchName);
        } catch (Exception e) {
            log.warn("Failed to mark commits as ANALYZED in DAG (non-critical): {}", e.getMessage());
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

        if (!unanalyzedCommits.isEmpty()) {
            try {
                gitGraphSyncService.markCommitsFailed(project.getId(), unanalyzedCommits);
                log.info("DAG: Marked {} commits as FAILED after branch analysis failure (branch={})",
                        unanalyzedCommits.size(), request.getTargetBranchName());
            } catch (Exception dagEx) {
                log.warn("Failed to mark commits as FAILED in DAG: {}", dagEx.getMessage());
            }
        }

        log.warn("Branch reconciliation failed (Branch: {}, Commit: {}): {}",
                request.getTargetBranchName(), request.getCommitHash(), e.getMessage());
    }
}
