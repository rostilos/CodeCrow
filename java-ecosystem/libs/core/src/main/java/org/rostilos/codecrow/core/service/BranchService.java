package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;
import java.util.stream.Collectors;

@Service
@Transactional
public class BranchService {

    private final BranchRepository branchRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final CodeAnalysisRepository codeAnalysisRepository;

    public BranchService(BranchRepository branchRepository,
                         BranchIssueRepository branchIssueRepository,
                         CodeAnalysisRepository codeAnalysisRepository) {
        this.branchRepository = branchRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.codeAnalysisRepository = codeAnalysisRepository;
    }

    public Optional<Branch> findByProjectIdAndBranchName(Long projectId, String branchName) {
        return branchRepository.findByProjectIdAndBranchName(projectId, branchName);
    }

    public List<Branch> findByProjectId(Long projectId) {
        return branchRepository.findByProjectId(projectId);
    }

    public List<BranchIssue> findIssuesByBranchId(Long branchId) {
        return branchIssueRepository.findByBranchId(branchId);
    }

    public BranchStats getBranchStats(Long projectId, String branchName) {
        Optional<Branch> branchOpt = branchRepository.findByProjectIdAndBranchName(projectId, branchName);

        if (branchOpt.isEmpty()) {
            return new BranchStats(0, 0, 0, 0, 0, 0, new ArrayList<>(), null, null);
        }

        Branch branch = branchOpt.get();
        List<BranchIssue> issues = branchIssueRepository.findByBranchId(branch.getId());

        List<Object[]> mostProblematicFiles = getMostProblematicFiles(issues);

        // Calculate counts directly from issues list for accuracy
        long openCount = issues.stream().filter(i -> !i.isResolved()).count();
        long resolvedCount = issues.stream().filter(BranchIssue::isResolved).count();
        long highCount = issues.stream().filter(i -> i.getSeverity() == org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.HIGH && !i.isResolved()).count();
        long mediumCount = issues.stream().filter(i -> i.getSeverity() == org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.MEDIUM && !i.isResolved()).count();
        long lowCount = issues.stream().filter(i -> i.getSeverity() == org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity.LOW && !i.isResolved()).count();

        return new BranchStats(
                openCount,
                highCount,
                mediumCount,
                lowCount,
                resolvedCount,
                1L,
                mostProblematicFiles,
                branch.getUpdatedAt(),
                branch.getCreatedAt()
        );
    }

    private List<Object[]> getMostProblematicFiles(List<BranchIssue> issues) {
        Map<String, Long> fileIssueCounts = issues.stream()
                .filter(bi -> !bi.isResolved())
                .collect(Collectors.groupingBy(
                        bi -> bi.getCodeAnalysisIssue().getFilePath(),
                        Collectors.counting()
                ));

        return fileIssueCounts.entrySet().stream()
                .sorted(Map.Entry.<String, Long>comparingByValue().reversed())
                .limit(10)
                .map(entry -> new Object[]{entry.getKey(), entry.getValue()})
                .toList();
    }

    public List<CodeAnalysis> getBranchAnalysisHistory(Long projectId, String branchName) {
        return codeAnalysisRepository.findByProjectIdAndBranchName(projectId, branchName);
    }

    public static class BranchStats {
        private final long totalIssues;
        private final long highSeverityCount;
        private final long mediumSeverityCount;
        private final long lowSeverityCount;
        private final long resolvedCount;
        private final long totalAnalyses;
        private final List<Object[]> mostProblematicFiles;
        private final java.time.OffsetDateTime lastAnalysisDate;
        private final java.time.OffsetDateTime firstAnalysisDate;

        public BranchStats(long totalIssues, long highSeverityCount, long mediumSeverityCount,
                          long lowSeverityCount, long resolvedCount, long totalAnalyses,
                          List<Object[]> mostProblematicFiles,
                          java.time.OffsetDateTime lastAnalysisDate,
                          java.time.OffsetDateTime firstAnalysisDate) {
            this.totalIssues = totalIssues;
            this.highSeverityCount = highSeverityCount;
            this.mediumSeverityCount = mediumSeverityCount;
            this.lowSeverityCount = lowSeverityCount;
            this.resolvedCount = resolvedCount;
            this.totalAnalyses = totalAnalyses;
            this.mostProblematicFiles = mostProblematicFiles;
            this.lastAnalysisDate = lastAnalysisDate;
            this.firstAnalysisDate = firstAnalysisDate;
        }

        public long getTotalIssues() { return totalIssues; }
        public long getHighSeverityCount() { return highSeverityCount; }
        public long getMediumSeverityCount() { return mediumSeverityCount; }
        public long getLowSeverityCount() { return lowSeverityCount; }
        public long getResolvedCount() { return resolvedCount; }
        public long getTotalAnalyses() { return totalAnalyses; }
        public List<Object[]> getMostProblematicFiles() { return mostProblematicFiles; }
        public java.time.OffsetDateTime getLastAnalysisDate() { return lastAnalysisDate; }
        public java.time.OffsetDateTime getFirstAnalysisDate() { return firstAnalysisDate; }
    }
}

