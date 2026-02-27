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

import org.springframework.dao.DataIntegrityViolationException;

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

        // ── Build branch-wide content fingerprint set ─────────────────────────
        // The unique constraint uq_branch_issue_content_fp is on (branch_id, content_fingerprint)
        // so dedup must be branch-wide, not per-file. Pre-load all existing fingerprints once.
        List<BranchIssue> allBranchIssues = branchIssueRepository.findByBranchId(branch.getId());

        Set<String> branchContentFingerprints = new HashSet<>();
        Set<Long> allLinkedOriginIds = new HashSet<>();
        for (BranchIssue bi : allBranchIssues) {
            if (bi.getContentFingerprint() != null) {
                branchContentFingerprints.add(bi.getContentFingerprint());
            }
            if (bi.getOriginIssue() != null) {
                allLinkedOriginIds.add(bi.getOriginIssue().getId());
            }
        }

        log.debug("Branch {} pre-loaded {} content fingerprints and {} origin IDs for dedup",
                branch.getBranchName(), branchContentFingerprints.size(), allLinkedOriginIds.size());

        // ── Per-file mapping loop ─────────────────────────────────────────────
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

            // Per-file legacy key map (legacy keys are file-scoped by construction)
            List<BranchIssue> existingBranchIssuesForFile = branchIssueRepository
                    .findByBranchIdAndFilePath(branch.getId(), filePath);

            Map<String, BranchIssue> legacyKeyMap = new HashMap<>();
            for (BranchIssue bi : existingBranchIssuesForFile) {
                legacyKeyMap.putIfAbsent(buildLegacyContentKey(bi), bi);
            }

            int skipped = 0;
            int mapped = 0;
            for (CodeAnalysisIssue issue : unresolvedIssues) {
                // Tier 1: origin ID match (branch-wide)
                if (allLinkedOriginIds.contains(issue.getId())) {
                    updateSeverityIfChanged(branch, issue);
                    continue;
                }

                // Tier 2: content fingerprint dedup (branch-wide — matches DB constraint scope)
                if (issue.getContentFingerprint() != null
                        && branchContentFingerprints.contains(issue.getContentFingerprint())) {
                    skipped++;
                    continue;
                }

                // Tier 3: legacy key dedup (per-file)
                String legacyKey = buildLegacyContentKeyFromCAI(issue);
                if (legacyKeyMap.containsKey(legacyKey)) {
                    skipped++;
                    continue;
                }

                // No match — create new BranchIssue as a full deep copy
                BranchIssue bi = BranchIssue.fromCodeAnalysisIssue(issue, branch);
                try {
                    branchIssueRepository.saveAndFlush(bi);
                } catch (DataIntegrityViolationException e) {
                    // Safety net: concurrent insert or edge-case fingerprint collision
                    log.warn("Duplicate content_fingerprint for branch {} file {} — skipping (fp={})",
                            branch.getId(), filePath,
                            bi.getContentFingerprint() != null ? bi.getContentFingerprint().substring(0, 12) + "..." : "null");
                    skipped++;
                    if (bi.getContentFingerprint() != null) {
                        branchContentFingerprints.add(bi.getContentFingerprint());
                    }
                    continue;
                }
                mapped++;

                // Register in branch-wide maps so subsequent issues also dedup
                if (bi.getContentFingerprint() != null) {
                    branchContentFingerprints.add(bi.getContentFingerprint());
                }
                legacyKeyMap.put(buildLegacyContentKey(bi), bi);
                allLinkedOriginIds.add(issue.getId());
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
