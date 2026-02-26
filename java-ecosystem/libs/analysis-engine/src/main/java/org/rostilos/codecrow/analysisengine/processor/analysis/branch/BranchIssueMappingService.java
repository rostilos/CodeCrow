package org.rostilos.codecrow.analysisengine.processor.analysis.branch;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.stream.Collectors;

/**
 * Handles mapping of {@link CodeAnalysisIssue} records to
 * {@link BranchIssue} records with content-based deduplication.
 */
@Service
public class BranchIssueMappingService {

    private static final Logger log = LoggerFactory.getLogger(BranchIssueMappingService.class);

    private final CodeAnalysisIssueRepository codeAnalysisIssueRepository;
    private final BranchIssueRepository branchIssueRepository;

    public BranchIssueMappingService(CodeAnalysisIssueRepository codeAnalysisIssueRepository,
                                     BranchIssueRepository branchIssueRepository) {
        this.codeAnalysisIssueRepository = codeAnalysisIssueRepository;
        this.branchIssueRepository = branchIssueRepository;
    }

    // ───────────────── CAI → BranchIssue mapping ─────────────────────────────

    /**
     * Maps all unresolved {@link CodeAnalysisIssue} records for the given
     * changed files to {@link BranchIssue} records on the branch, using
     * three-tier content-based deduplication (origin ID, content fingerprint,
     * legacy key).
     */
    public void mapCodeAnalysisIssuesToBranch(Set<String> changedFiles,
                                              Set<String> filesExistingInBranch,
                                              Branch branch, Project project) {
        for (String filePath : changedFiles) {
            if (!filesExistingInBranch.contains(filePath)) {
                log.debug("Skipping issue mapping for file {} - does not exist in branch {} (cached)",
                        filePath, branch.getBranchName());
                continue;
            }

            List<CodeAnalysisIssue> allIssues = codeAnalysisIssueRepository
                    .findByProjectIdAndFilePath(project.getId(), filePath);

            List<CodeAnalysisIssue> unresolvedIssues = allIssues.stream()
                    .filter(issue -> !issue.isResolved())
                    .toList();

            if (!unresolvedIssues.isEmpty()) {
                log.debug("Found {} unresolved CodeAnalysisIssues for file {} (out of {} total)",
                        unresolvedIssues.size(), filePath, allIssues.size());
            }

            // Build deduplication maps from ALL existing BranchIssues (resolved + unresolved)
            List<BranchIssue> existingBranchIssues = branchIssueRepository
                    .findByBranchIdAndFilePath(branch.getId(), filePath);

            Map<String, BranchIssue> contentFpMap = new HashMap<>();
            Map<String, BranchIssue> legacyKeyMap = new HashMap<>();
            for (BranchIssue bi : existingBranchIssues) {
                if (bi.getContentFingerprint() != null) {
                    contentFpMap.putIfAbsent(bi.getContentFingerprint(), bi);
                }
                legacyKeyMap.putIfAbsent(buildLegacyContentKey(bi), bi);
            }

            Set<Long> linkedOriginIds = existingBranchIssues.stream()
                    .filter(bi -> bi.getOriginIssue() != null)
                    .map(bi -> bi.getOriginIssue().getId())
                    .collect(Collectors.toSet());

            int skipped = 0;
            int mapped = 0;
            for (CodeAnalysisIssue issue : unresolvedIssues) {
                // Tier 1: origin ID match
                if (linkedOriginIds.contains(issue.getId())) {
                    updateSeverityIfChanged(branch, issue);
                    continue;
                }

                // Tier 2: content fingerprint dedup
                if (issue.getContentFingerprint() != null
                        && contentFpMap.containsKey(issue.getContentFingerprint())) {
                    skipped++;
                    continue;
                }

                // Tier 3: legacy key dedup
                String legacyKey = buildLegacyContentKeyFromCAI(issue);
                if (legacyKeyMap.containsKey(legacyKey)) {
                    skipped++;
                    continue;
                }

                // No match — create new BranchIssue as a full deep copy
                BranchIssue bi = BranchIssue.fromCodeAnalysisIssue(issue, branch);
                branchIssueRepository.saveAndFlush(bi);
                mapped++;

                // Register in maps so subsequent issues in this batch also dedup
                if (bi.getContentFingerprint() != null) {
                    contentFpMap.put(bi.getContentFingerprint(), bi);
                }
                legacyKeyMap.put(buildLegacyContentKey(bi), bi);
                linkedOriginIds.add(issue.getId());
            }

            if (mapped > 0 || skipped > 0) {
                log.info("Issue mapping for file {} in branch {}: {} mapped, {} skipped (dedup)",
                        filePath, branch.getBranchName(), mapped, skipped);
            }
        }
    }

    // ───────────────── PR issue path lookup ──────────────────────────────────

    /**
     * Returns the set of file paths with unresolved issues from a merged PR.
     * Used to augment the changed-files set so that the branch analysis doesn't
     * miss issues that the diff didn't cover.
     */
    public Set<String> findPrIssuePaths(Long projectId, Long prNumber) {
        List<CodeAnalysisIssue> prIssues = codeAnalysisIssueRepository
                .findByProjectIdAndPrNumber(projectId, prNumber);
        return prIssues.stream()
                .filter(i -> !i.isResolved())
                .map(CodeAnalysisIssue::getFilePath)
                .filter(fp -> fp != null && !fp.isBlank())
                .collect(Collectors.toSet());
    }

    // ───────────────── Deduplication key builders ────────────────────────────

    /**
     * Builds a legacy content key for deduplication of branch issues
     * (pre-tracking fallback).
     */
    public static String buildLegacyContentKey(BranchIssue bi) {
        return bi.getFilePath() + ":" +
                bi.getLineNumber() + ":" +
                bi.getSeverity() + ":" +
                bi.getIssueCategory();
    }

    /**
     * Builds a legacy content key from a {@link CodeAnalysisIssue}.
     */
    public static String buildLegacyContentKeyFromCAI(CodeAnalysisIssue issue) {
        return issue.getFilePath() + ":" +
                issue.getLineNumber() + ":" +
                issue.getSeverity() + ":" +
                issue.getIssueCategory();
    }

    // ───────────────── Private helpers ───────────────────────────────────────

    private void updateSeverityIfChanged(Branch branch, CodeAnalysisIssue issue) {
        branchIssueRepository.findByBranchIdAndOriginIssueId(branch.getId(), issue.getId())
                .ifPresent(existing -> {
                    if (existing.getSeverity() != issue.getSeverity()) {
                        existing.setSeverity(issue.getSeverity());
                        branchIssueRepository.saveAndFlush(existing);
                    }
                });
    }
}
