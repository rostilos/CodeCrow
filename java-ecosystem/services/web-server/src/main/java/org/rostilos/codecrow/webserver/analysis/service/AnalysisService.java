package org.rostilos.codecrow.webserver.analysis.service;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;
import java.util.stream.Collectors;

@Service
@Transactional
public class AnalysisService {

    private static final Logger log = LoggerFactory.getLogger(AnalysisService.class);

    private final CodeAnalysisService codeAnalysisService;
    private final CodeAnalysisIssueRepository issueRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final BranchRepository branchRepository;

    public AnalysisService(
            CodeAnalysisService codeAnalysisService,
            CodeAnalysisIssueRepository issueRepository,
            BranchIssueRepository branchIssueRepository,
            BranchRepository branchRepository
    ) {
        this.codeAnalysisService = codeAnalysisService;
        this.issueRepository = issueRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.branchRepository = branchRepository;
    }

    /**
     * Find issues for a project with optional filters.
     * - If pullRequestId is provided, prefer the analysis attached to that PR (prNumber).
     * - Otherwise, filter analyses by branch (if provided) and collect issues.
     * - Filter by severity and issue category (type) if provided.
     */
    public List<CodeAnalysisIssue> findIssues(
            Long projectId,
            String branch,
            String pullRequestId,
            String severity,
            String type,
            int prVersion
    ) {
        List<CodeAnalysis> analyses = new ArrayList<>();

        if (pullRequestId != null && !pullRequestId.isBlank()) {
            try {
                Long prNum = Long.parseLong(pullRequestId);
                Optional<CodeAnalysis> pa = codeAnalysisService.findByProjectIdAndPrNumberAndPrVersion(projectId, prNum, prVersion);
                pa.ifPresent(analyses::add);
            } catch (NumberFormatException ignored) {
                // fall back to scanning all analyses
                analyses = codeAnalysisService.findByProjectId(projectId);
            }
        } else {
            analyses = codeAnalysisService.findByProjectId(projectId);
            if (branch != null && !branch.isBlank()) {
                analyses = analyses.stream()
                        .filter(a -> branch.equals(a.getBranchName()))
                        .toList();
            }
        }

        // collect issues from selected analyses
        List<CodeAnalysisIssue> issues = analyses.stream()
                .flatMap(a -> a.getIssues().stream())
                .toList();

        // filter by severity if requested (supports "critical" -> HIGH mapping)
        if (severity != null && !severity.isBlank()) {
            String sev = severity.trim().toLowerCase();
            IssueSeverity target = null;
            if ("critical".equals(sev)) {
                target = IssueSeverity.HIGH;
            } else {
                try {
                    target = IssueSeverity.valueOf(sev.toUpperCase());
                } catch (IllegalArgumentException e) {
                    // unknown, ignore severity filter
                    target = null;
                }
            }
            if (target != null) {
                IssueSeverity finalTarget = target;
                issues = issues.stream()
                        .filter(i -> i.getSeverity() == finalTarget)
                        .toList();
            }
        }

        // filter by issue type (mapped from issueCategory on CodeAnalysisIssue)
        if (type != null && !type.isBlank()) {
            String t = type.trim().toUpperCase().replace(" ", "_").replace("-", "_");
            issues = issues.stream()
                    .filter(i -> i.getIssueCategory() != null && i.getIssueCategory().name().equals(t))
                    .toList();
        }

        return issues;
    }

    /**
     * Update issue status: resolved|ignored|reopened
     * Also updates all related BranchIssue records to keep them in sync.
     */
    public boolean updateIssueStatus(Long issueId, boolean isResolved, String comment, String actor) {
        log.info("updateIssueStatus called: issueId={}, isResolved={}", issueId, isResolved);

        Optional<CodeAnalysisIssue> oi = issueRepository.findById(issueId);
        if (oi.isEmpty()) {
            log.warn("updateIssueStatus: CodeAnalysisIssue not found for id={}", issueId);
            return false;
        }

        CodeAnalysisIssue issue = oi.get();
        log.info("updateIssueStatus: Found issue id={}, current isResolved={}", issue.getId(), issue.isResolved());

        issue.setResolved(isResolved);

        // optionally append the comment into reason/suggestedFix (not overwriting)
        if (comment != null && !comment.isBlank()) {
            String prev = issue.getReason() == null ? "" : issue.getReason();
            String appended = prev + "\n[status change by " + (actor == null ? "system" : actor) + "]: " + comment;
            issue.setReason(appended);
        }

        issueRepository.save(issue);
        log.info("updateIssueStatus: Saved CodeAnalysisIssue id={}, isResolved={}", issue.getId(), issue.isResolved());

        List<BranchIssue> branchIssues = branchIssueRepository.findByCodeAnalysisIssueId(issueId);
        log.info("updateIssueStatus: Found {} BranchIssue records for codeAnalysisIssueId={}", branchIssues.size(), issueId);

        if (!branchIssues.isEmpty()) {
            for (BranchIssue branchIssue : branchIssues) {
                branchIssue.setResolved(isResolved);
            }
            branchIssueRepository.saveAll(branchIssues);
            // Flush to ensure changes are visible for subsequent queries
            branchIssueRepository.flush();
            log.info("updateIssueStatus: Updated {} BranchIssue records", branchIssues.size());

            //Issue counts on Branch need to be updated as well
            Set<Long> branchIds = branchIssues.stream()
                    .map(bi -> bi.getBranch().getId())
                    .collect(Collectors.toSet());
            
            for (Long branchId : branchIds) {
                branchRepository.findByIdWithIssues(branchId).ifPresent(branch -> {
                    branch.updateIssueCounts();
                    branchRepository.save(branch);
                });
            }
        }

        return true;
    }

    public Optional<CodeAnalysisIssue> findIssueById(Long issueId) {
        return issueRepository.findById(issueId);
    }
}
