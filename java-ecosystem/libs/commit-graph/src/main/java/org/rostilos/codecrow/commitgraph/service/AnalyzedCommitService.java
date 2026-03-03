package org.rostilos.codecrow.commitgraph.service;

import org.rostilos.codecrow.commitgraph.model.AnalyzedCommit;
import org.rostilos.codecrow.commitgraph.persistence.AnalyzedCommitRepository;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * Manages the {@link AnalyzedCommit} table — the simple lookup that tracks
 * which commits have already been analyzed.
 * <p>
 * Replaces the old {@code GitGraphSyncService} which maintained a full DAG
 * in the database. This service is much simpler: it just records and queries
 * analyzed commit hashes.
 */
@Service
public class AnalyzedCommitService {

    private static final Logger log = LoggerFactory.getLogger(AnalyzedCommitService.class);

    private final AnalyzedCommitRepository analyzedCommitRepository;

    public AnalyzedCommitService(AnalyzedCommitRepository analyzedCommitRepository) {
        this.analyzedCommitRepository = analyzedCommitRepository;
    }

    /**
     * Record a list of commits as analyzed after a successful branch analysis.
     *
     * @param project   the project
     * @param hashes    the commit hashes to record
     */
    @Transactional
    public void recordBranchCommitsAnalyzed(Project project, List<String> hashes) {
        if (hashes == null || hashes.isEmpty()) return;

        Set<String> alreadyAnalyzed = analyzedCommitRepository
                .findAnalyzedHashesByProjectIdAndCommitHashIn(project.getId(), hashes);

        List<AnalyzedCommit> toSave = new ArrayList<>();
        for (String hash : hashes) {
            if (!alreadyAnalyzed.contains(hash)) {
                toSave.add(new AnalyzedCommit(project, hash, AnalysisType.BRANCH_ANALYSIS));
            }
        }

        if (!toSave.isEmpty()) {
            analyzedCommitRepository.saveAll(toSave);
            log.info("Recorded {} commits as analyzed (branch analysis, projectId={})",
                    toSave.size(), project.getId());
        }
    }

    /**
     * Record a list of commits as analyzed after a successful PR analysis.
     *
     * @param project   the project
     * @param hashes    the commit hashes to record
     * @param analysis  the CodeAnalysis that covered these commits (may be null)
     */
    @Transactional
    public void recordPrCommitsAnalyzed(Project project, List<String> hashes, CodeAnalysis analysis) {
        if (hashes == null || hashes.isEmpty()) return;

        Set<String> alreadyAnalyzed = analyzedCommitRepository
                .findAnalyzedHashesByProjectIdAndCommitHashIn(project.getId(), hashes);

        Long analysisId = analysis != null ? analysis.getId() : null;
        List<AnalyzedCommit> toSave = new ArrayList<>();
        for (String hash : hashes) {
            if (!alreadyAnalyzed.contains(hash)) {
                toSave.add(new AnalyzedCommit(project, hash, analysisId, AnalysisType.PR_REVIEW));
            }
        }

        if (!toSave.isEmpty()) {
            analyzedCommitRepository.saveAll(toSave);
            log.info("Recorded {} commits as analyzed (PR analysis, projectId={}, analysisId={})",
                    toSave.size(), project.getId(), analysisId);
        }
    }

    /**
     * Given a list of commit hashes, return those that have NOT been analyzed yet.
     *
     * @param projectId the project ID
     * @param allHashes all commit hashes to check
     * @return list of hashes not yet in the analyzed_commit table
     */
    public List<String> filterUnanalyzed(Long projectId, List<String> allHashes) {
        if (allHashes == null || allHashes.isEmpty()) return List.of();

        Set<String> analyzed = analyzedCommitRepository
                .findAnalyzedHashesByProjectIdAndCommitHashIn(projectId, allHashes);

        return allHashes.stream()
                .filter(h -> !analyzed.contains(h))
                .toList();
    }

    /**
     * Check if a specific commit has been analyzed.
     */
    public boolean isAnalyzed(Long projectId, String commitHash) {
        return analyzedCommitRepository.existsByProjectIdAndCommitHash(projectId, commitHash);
    }
}
