package org.rostilos.codecrow.analysisengine.service.branch;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.util.tracking.AnchoredIssueIdentity;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.stream.Collectors;

/**
 * Handles mapping of {@link CodeAnalysisIssue} records to
 * {@link BranchIssue} records with exact anchored-identity deduplication.
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
     * changed files to {@link BranchIssue} records on the branch. Only an exact
     * origin ID or a file-scoped, source-anchored identity may suppress a row.
     */
    public void mapCodeAnalysisIssuesToBranch(Set<String> changedFiles,
                                              Set<String> filesExistingInBranch,
                                              Branch branch, Project project) {
        mapCodeAnalysisIssuesToBranch(changedFiles, filesExistingInBranch, branch, project, Set.of());
    }

    /**
     * Maps unresolved issues to a branch. When {@code sourcePrNumber} is present,
     * mapping is scoped to that PR and ordered newest PR iteration first, so a merge
     * only carries the logical issues from the PR that actually arrived.
     */
    public void mapCodeAnalysisIssuesToBranch(Set<String> changedFiles,
                                              Set<String> filesExistingInBranch,
                                              Branch branch, Project project,
                                              Long sourcePrNumber) {
        Set<Long> sourcePrNumbers = sourcePrNumber == null
                ? Set.of()
                : Set.of(sourcePrNumber);
        mapCodeAnalysisIssuesToBranch(
                changedFiles, filesExistingInBranch, branch, project, sourcePrNumbers);
    }

    /**
     * Maps one completed merge batch in a single branch-wide deduplication pass.
     * Empty PR scope retains the direct-push behavior and reads branch analyses.
     */
    public void mapCodeAnalysisIssuesToBranch(Set<String> changedFiles,
                                              Set<String> filesExistingInBranch,
                                              Branch branch, Project project,
                                              Set<Long> sourcePrNumbers) {
        Set<Long> prNumbers = sourcePrNumbers == null
                ? Set.of()
                : Set.copyOf(sourcePrNumbers);

        // The database index is branch-wide, so its stored value must itself
        // contain exact file identity. Historical rows carry the older raw
        // content fingerprint; recompute from their source fields rather than
        // trusting the persisted representation.
        List<BranchIssue> allBranchIssues = branchIssueRepository.findByBranchId(branch.getId());

        Set<String> unresolvedAnchoredIdentities = new HashSet<>();
        Set<Long> allLinkedOriginIds = new HashSet<>();
        List<BranchIssue> resolvedRowsHoldingIdentity = new ArrayList<>();
        for (BranchIssue bi : allBranchIssues) {
            if (!bi.isResolved()) {
                String identity = AnchoredIssueIdentity.forBranchStorage(bi);
                if (identity != null) {
                    unresolvedAnchoredIdentities.add(identity);
                }
            } else if (bi.getContentFingerprint() != null) {
                // Compatibility cleanup for rows resolved before BranchIssue began
                // releasing the active-identity index in setResolved(true).
                bi.setContentFingerprint(null);
                resolvedRowsHoldingIdentity.add(bi);
            }
            if (bi.getOriginIssue() != null && bi.getOriginIssue().getId() != null) {
                allLinkedOriginIds.add(bi.getOriginIssue().getId());
            }
        }
        if (!resolvedRowsHoldingIdentity.isEmpty()) {
            branchIssueRepository.saveAllAndFlush(resolvedRowsHoldingIdentity);
            log.info(
                    "Released {} resolved branch identities for branch {}",
                    resolvedRowsHoldingIdentity.size(),
                    branch.getBranchName()
            );
        }

        log.debug(
                "Branch {} pre-loaded {} unresolved anchored identities and {} origin IDs",
                branch.getBranchName(),
                unresolvedAnchoredIdentities.size(),
                allLinkedOriginIds.size()
        );

        // ── Per-file mapping loop ─────────────────────────────────────────────
        for (String filePath : changedFiles.stream().sorted().toList()) {
            if (!filesExistingInBranch.contains(filePath)) {
                log.debug("Skipping issue mapping for file {} - does not exist in branch {} (cached)",
                        filePath, branch.getBranchName());
                continue;
            }

            List<CodeAnalysisIssue> allIssues;
            if (!prNumbers.isEmpty()) {
                allIssues = prNumbers.size() == 1
                        ? codeAnalysisIssueRepository.findByProjectIdAndPrNumberAndFilePathNewestFirst(
                                project.getId(), prNumbers.iterator().next(), filePath)
                        : codeAnalysisIssueRepository.findByProjectIdAndPrNumberInAndFilePathNewestFirst(
                                project.getId(), prNumbers, filePath);
            } else {
                // Scope to current branch — prevents pulling in issues from unrelated
                // branches / PRs that happen to touch the same file.
                allIssues = codeAnalysisIssueRepository
                        .findByProjectIdAndBranchNameAndFilePath(
                                project.getId(), branch.getBranchName(), filePath);
            }

            List<CodeAnalysisIssue> logicalIssues = !prNumbers.isEmpty()
                    ? filterShadowedPrLineageIssues(allIssues)
                    : allIssues;

            List<CodeAnalysisIssue> unresolvedIssues = logicalIssues.stream()
                    .filter(issue -> !issue.isResolved())
                    .toList();

            if (!unresolvedIssues.isEmpty()) {
                log.debug("Found {} unresolved CodeAnalysisIssues for file {} (out of {} total)",
                        unresolvedIssues.size(), filePath, allIssues.size());
            }

            int skipped = 0;
            int mapped = 0;
            for (CodeAnalysisIssue issue : unresolvedIssues) {
                // Exact provenance is authoritative, but a null ID is never an
                // identity and must not suppress another transient row.
                if (issue.getId() != null && allLinkedOriginIds.contains(issue.getId())) {
                    updateSeverityIfChanged(branch, issue);
                    continue;
                }

                String branchIdentity = AnchoredIssueIdentity.forBranchStorage(issue);
                if (branchIdentity != null
                        && unresolvedAnchoredIdentities.contains(branchIdentity)) {
                    skipped++;
                    continue;
                }

                // No match — create new BranchIssue as a full deep copy
                BranchIssue bi = BranchIssue.fromCodeAnalysisIssue(issue, branch);
                // The existing branch-wide partial unique index remains useful,
                // but only with this path-aware anchored storage identity. Null
                // deliberately bypasses the index for unanchored findings.
                bi.setContentFingerprint(branchIdentity);
                // A database conflict is not swallowed: after flush, the current
                // transaction cannot safely recover, and pretending success could
                // hide a finding. The caller receives the failure and may retry.
                branchIssueRepository.saveAndFlush(bi);
                mapped++;

                if (branchIdentity != null) {
                    unresolvedAnchoredIdentities.add(branchIdentity);
                }
                if (issue.getId() != null) {
                    allLinkedOriginIds.add(issue.getId());
                }
            }

            if (mapped > 0 || skipped > 0) {
                log.info("Issue mapping for file {} in branch {}: {} mapped, {} skipped (dedup)",
                        filePath, branch.getBranchName(), mapped, skipped);
            }
        }
    }

    /**
     * Keep only the latest row for each tracked logical PR issue.
     * <p>
     * PR iteration rows are immutable history. If a newer row has
     * {@code trackedFromIssueId}, every ancestor is superseded even when an older
     * ancestor still has {@code resolved=false}. That prevents fixed issues from
     * early PR iterations from being mapped to the branch after merge.
     */
    private List<CodeAnalysisIssue> filterShadowedPrLineageIssues(List<CodeAnalysisIssue> allIssues) {
        if (allIssues == null || allIssues.isEmpty()) {
            return List.of();
        }

        Map<Long, CodeAnalysisIssue> byId = allIssues.stream()
                .filter(issue -> issue.getId() != null)
                .collect(Collectors.toMap(
                        CodeAnalysisIssue::getId,
                        issue -> issue,
                        (first, ignored) -> first));

        Set<Long> shadowedAncestorIds = new HashSet<>();
        for (CodeAnalysisIssue issue : allIssues) {
            Long ancestorId = issue.getTrackedFromIssueId();
            while (ancestorId != null && shadowedAncestorIds.add(ancestorId)) {
                CodeAnalysisIssue ancestor = byId.get(ancestorId);
                ancestorId = ancestor != null ? ancestor.getTrackedFromIssueId() : null;
            }
        }

        return allIssues.stream()
                .filter(issue -> issue.getId() == null || !shadowedAncestorIds.contains(issue.getId()))
                .toList();
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

    /** Returns unresolved issue paths across every PR in a completed merge batch. */
    public Set<String> findPrIssuePaths(Long projectId, Set<Long> prNumbers) {
        if (prNumbers == null || prNumbers.isEmpty()) {
            return Set.of();
        }
        return codeAnalysisIssueRepository.findByProjectIdAndPrNumberIn(
                        projectId, Set.copyOf(prNumbers)).stream()
                .filter(i -> !i.isResolved())
                .map(CodeAnalysisIssue::getFilePath)
                .filter(fp -> fp != null && !fp.isBlank())
                .collect(Collectors.toSet());
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
