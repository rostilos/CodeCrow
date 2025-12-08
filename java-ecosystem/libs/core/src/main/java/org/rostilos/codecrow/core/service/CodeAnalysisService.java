package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Service
@Transactional
public class CodeAnalysisService {

    private final CodeAnalysisRepository analysisRepository;
    private final CodeAnalysisIssueRepository issueRepository;
    private final CodeAnalysisRepository codeAnalysisRepository;
    private static final Logger log = LoggerFactory.getLogger(CodeAnalysisService.class);

    @Autowired
    public CodeAnalysisService(
            CodeAnalysisRepository analysisRepository,
            CodeAnalysisIssueRepository issueRepository,
            CodeAnalysisRepository codeAnalysisRepository
    ) {
        this.analysisRepository = analysisRepository;
        this.issueRepository = issueRepository;
        this.codeAnalysisRepository = codeAnalysisRepository;
    }

    public CodeAnalysis createAnalysisFromAiResponse(
            Project project,
            Map<String, Object> analysisData,
            Long pullRequestId,
            String targetBranchName,
            String sourceBranchName,
            String commitHash
    ) {
        try {
            // Check if analysis already exists for this commit (handles webhook retries)
            Optional<CodeAnalysis> existingAnalysis = codeAnalysisRepository
                    .findByProjectIdAndCommitHashAndPrNumber(project.getId(), commitHash, pullRequestId);
            
            if (existingAnalysis.isPresent()) {
                log.info("Analysis already exists for project={}, commit={}, pr={}. Returning existing.",
                        project.getId(), commitHash, pullRequestId);
                return existingAnalysis.get();
            }
            
            CodeAnalysis analysis = new CodeAnalysis();
            int previousVersion = codeAnalysisRepository.findMaxPrVersion(project.getId(), pullRequestId).orElse(0);
            analysis.setProject(project);
            analysis.setAnalysisType(AnalysisType.PR_REVIEW);
            analysis.setPrNumber(pullRequestId);
            analysis.setBranchName(targetBranchName);
            analysis.setSourceBranchName(sourceBranchName);
            analysis.setPrVersion(previousVersion + 1);

            return fillAnalysisData(analysis, analysisData, commitHash);
        } catch (Exception e) {
            log.error("Error creating analysis from AI response: {}", e.getMessage(), e);
            throw new RuntimeException("Failed to create analysis from AI response", e);
        }
    }



    private CodeAnalysis fillAnalysisData(
            CodeAnalysis analysis,
            Map<String, Object> analysisData,
            String commitHash
    ) {
        try {
            analysis.setCommitHash(commitHash);
            analysis.setStatus(AnalysisStatus.ACCEPTED);

            // Extract comment from the analysis data
            String comment = (String) analysisData.get("comment");
            if (comment == null) {
                log.warn("No comment found in analysis data");
                comment = "No comment provided";
            }
            analysis.setComment(comment);

            // Extract issues from the analysis data - handle both List and Map formats
            Object issuesObj = analysisData.get("issues");
            if (issuesObj == null) {
                log.warn("No issues found in analysis data");
                return analysisRepository.save(analysis);
            }

            // Handle issues as List (array format from AI)
            if (issuesObj instanceof List) {
                List<Object> issuesList = (List<Object>) issuesObj;
                log.info("Processing {} issues from AI analysis (array format)", issuesList.size());
                
                for (int i = 0; i < issuesList.size(); i++) {
                    try {
                        Map<String, Object> issueData = (Map<String, Object>) issuesList.get(i);
                        if (issueData == null) {
                            log.warn("Null issue data at index: {}", i);
                            continue;
                        }
                        CodeAnalysisIssue issue = createIssueFromData(issueData, String.valueOf(i));
                        if (issue != null) {
                            analysis.addIssue(issue);
                        }
                    } catch (Exception e) {
                        log.error("Error processing issue at index '{}': {}", i, e.getMessage(), e);
                    }
                }
            }
            // Handle issues as Map (legacy object format with numeric keys)
            else if (issuesObj instanceof Map) {
                Map<String, Object> issues = (Map<String, Object>) issuesObj;
                log.info("Processing {} issues from AI analysis (map format)", issues.size());

                for (Map.Entry<String, Object> entry : issues.entrySet()) {
                    try {
                        Map<String, Object> issueData = (Map<String, Object>) entry.getValue();

                        if (issueData == null) {
                            log.warn("Null issue data for key: {}", entry.getKey());
                            continue;
                        }

                        CodeAnalysisIssue issue = createIssueFromData(issueData, entry.getKey());
                        if (issue != null) {
                            analysis.addIssue(issue);
                        }
                    } catch (Exception e) {
                        log.error("Error processing issue with key '{}': {}", entry.getKey(), e.getMessage(), e);
                    }
                }
            } else {
                log.warn("Issues field is neither List nor Map: {}", issuesObj.getClass().getName());
            }

            log.info("Successfully created analysis with {} issues", analysis.getIssues().size());
            return analysisRepository.save(analysis);

        } catch (Exception e) {
            log.error("Error creating analysis from AI response: {}", e.getMessage(), e);
            throw new RuntimeException("Failed to create analysis from AI response", e);
        }
    }

    private CodeAnalysis removePreviousAnalysisData(CodeAnalysis codeAnalysis) {
        try {
            codeAnalysis.getIssues().clear();
            codeAnalysis.updateIssueCounts();
            return codeAnalysisRepository.save(codeAnalysis);

        } catch (Exception e) {
            log.error("Failed to remove previous analysis: {}", e.getMessage(), e);
            throw new RuntimeException("Failed to remove previous analysis", e);
        }
    }

    public Optional<CodeAnalysis> getCodeAnalysisCache(Long projectId, String commitHash, Long prNumber) {
        return codeAnalysisRepository.findByProjectIdAndCommitHashAndPrNumber(projectId, commitHash, prNumber).stream().findFirst();
    }

    public Optional<CodeAnalysis> getPreviousVersionCodeAnalysis(Long projectId, Long prNumber) {
        return codeAnalysisRepository.findByProjectIdAndPrNumberWithMaxPrVersion(projectId, prNumber);
    }

    public int getMaxAnalysisPrVersion(Long projectId, Long prNumber) {
        return codeAnalysisRepository.findMaxPrVersion(projectId, prNumber).orElse(0);
    }

    private CodeAnalysisIssue createIssueFromData(Map<String, Object> issueData, String issueKey) {
        try {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();

            String severityStr = (String) issueData.get("severity");
            if (severityStr == null) {
                log.warn("No severity found for issue {}", issueKey);
                return null;
            }

            try {
                issue.setSeverity(IssueSeverity.valueOf(severityStr.toUpperCase()));
            } catch (IllegalArgumentException e) {
                log.warn("Invalid severity '{}' for issue {}, defaulting to MEDIUM", severityStr, issueKey);
                issue.setSeverity(IssueSeverity.MEDIUM);
            }

            // Set file path
            String filePath = (String) issueData.get("file");
            if (filePath == null || filePath.trim().isEmpty()) {
                log.warn("No file path found for issue {}", issueKey);
                filePath = "unknown";
            }
            issue.setFilePath(filePath);

            // Set line number
            Object lineObj = issueData.get("line");
            if (lineObj != null) {
                try {
                    if (lineObj instanceof String) {
                        issue.setLineNumber(Integer.parseInt((String) lineObj));
                    } else if (lineObj instanceof Number) {
                        issue.setLineNumber(((Number) lineObj).intValue());
                    }
                } catch (NumberFormatException e) {
                    log.warn("Invalid line number '{}' for issue {}", lineObj, issueKey);
                    issue.setLineNumber(0);
                }
            } else {
                issue.setLineNumber(0);
            }

            // Set reason
            String reason = (String) issueData.get("reason");
            if (reason == null || reason.trim().isEmpty()) {
                log.warn("No reason found for issue {}", issueKey);
                reason = "No reason provided";
            }
            issue.setReason(reason);

            String suggestedFixDescription = (String) issueData.get("suggestedFixDescription");
            if (suggestedFixDescription == null) {
                suggestedFixDescription = "No suggested fix description provided";
            }
            issue.setSuggestedFixDescription(suggestedFixDescription);


            String suggestedFixDiff = (String) issueData.get("suggestedFixDiff");
            if (suggestedFixDiff == null) {
                suggestedFixDiff = "No suggested fix provided";
            }
            issue.setSuggestedFixDiff(suggestedFixDiff);

            boolean isResolved = (boolean) issueData.get("isResolved");
            issue.setResolved(isResolved);

            String categoryStr = (String) issueData.get("category");
            if (categoryStr != null && !categoryStr.isBlank()) {
                issue.setIssueCategory(IssueCategory.fromString(categoryStr));
            } else {
                issue.setIssueCategory(IssueCategory.CODE_QUALITY);
            }

            log.debug("Created issue: {} severity, category: {}, file: {}, line: {}",
                    issue.getSeverity(), issue.getIssueCategory(), issue.getFilePath(), issue.getLineNumber());

            return issue;

        } catch (Exception e) {
            log.error("Error creating issue from data for key '{}': {}", issueKey, e.getMessage(), e);
            return null;
        }
    }

    public CodeAnalysis createAnalysis(Project project, AnalysisType analysisType) {
        CodeAnalysis analysis = new CodeAnalysis();
        analysis.setProject(project);
        analysis.setAnalysisType(analysisType);
        analysis.setStatus(AnalysisStatus.PENDING);
        return analysisRepository.save(analysis);
    }

    public CodeAnalysis saveAnalysis(CodeAnalysis analysis) {
        analysis.updateIssueCounts();
        return analysisRepository.save(analysis);
    }

    public Optional<CodeAnalysis> findById(Long id) {
        return analysisRepository.findById(id);
    }

    public List<CodeAnalysis> findByProjectId(Long projectId) {
        return analysisRepository.findByProjectIdOrderByCreatedAtDesc(projectId);
    }

    public List<CodeAnalysis> findByProjectIdAndType(Long projectId, AnalysisType analysisType) {
        return analysisRepository.findByProjectIdAndAnalysisTypeOrderByCreatedAtDesc(projectId, analysisType);
    }

    public Optional<CodeAnalysis> findByProjectIdAndPrNumber(Long projectId, Long prNumber) {
        return analysisRepository.findByProjectIdAndPrNumber(projectId, prNumber);
    }

    public Optional<CodeAnalysis> findByProjectIdAndPrNumberAndPrVersion(Long projectId, Long prNumber, int prVersion) {
        return analysisRepository.findByProjectIdAndPrNumberAndPrVersion(projectId, prNumber, prVersion);
    }

    public List<CodeAnalysis> findByProjectIdAndDateRange(Long projectId, OffsetDateTime startDate, OffsetDateTime endDate) {
        return analysisRepository.findByProjectIdAndDateRange(projectId, startDate, endDate);
    }

    public List<CodeAnalysis> findByProjectIdWithHighSeverityIssues(Long projectId) {
        return analysisRepository.findByProjectIdWithHighSeverityIssues(projectId);
    }

    public Optional<CodeAnalysis> findLatestByProjectId(Long projectId) {
        return analysisRepository.findLatestByProjectId(projectId);
    }

    public AnalysisStats getProjectAnalysisStats(Long projectId) {
        long totalAnalyses = analysisRepository.countByProjectId(projectId);
        Double avgIssues = analysisRepository.getAverageIssuesPerAnalysis(projectId);

        long highSeverityCount = issueRepository.countByProjectIdAndSeverity(projectId, IssueSeverity.HIGH);
        long mediumSeverityCount = issueRepository.countByProjectIdAndSeverity(projectId, IssueSeverity.MEDIUM);
        long lowSeverityCount = issueRepository.countByProjectIdAndSeverity(projectId, IssueSeverity.LOW);

        List<Object[]> problematicFiles = issueRepository.findMostProblematicFilesByProjectId(projectId);

        return new AnalysisStats(totalAnalyses, avgIssues != null ? avgIssues : 0.0,
                highSeverityCount, mediumSeverityCount, lowSeverityCount, problematicFiles);
    }

    public CodeAnalysisIssue saveIssue(CodeAnalysisIssue issue) {
        return issueRepository.save(issue);
    }

    public List<CodeAnalysisIssue> findIssuesByAnalysisId(Long analysisId) {
        return issueRepository.findByAnalysisIdOrderBySeverityDescLineNumberAsc(analysisId);
    }

    public List<CodeAnalysisIssue> findIssuesByAnalysisIdAndSeverity(Long analysisId, IssueSeverity severity) {
        return issueRepository.findByAnalysisIdAndSeverityOrderByLineNumberAsc(analysisId, severity);
    }

    public void markIssueAsResolved(Long issueId) {
        issueRepository.findById(issueId).ifPresent(issue -> {
            issue.setResolved(true);
            issueRepository.save(issue);
        });
    }

    public void deleteAnalysis(Long analysisId) {
        analysisRepository.deleteById(analysisId);
    }

    public void deleteAllAnalysesByProjectId(Long projectId) {
        analysisRepository.deleteByProjectId(projectId);
    }

    public static class AnalysisStats {
        private final long totalAnalyses;
        private final double averageIssuesPerAnalysis;
        private final long highSeverityCount;
        private final long mediumSeverityCount;
        private final long lowSeverityCount;
        private final List<Object[]> mostProblematicFiles;

        public AnalysisStats(long totalAnalyses, double averageIssuesPerAnalysis,
                             long highSeverityCount, long mediumSeverityCount, long lowSeverityCount,
                             List<Object[]> mostProblematicFiles) {
            this.totalAnalyses = totalAnalyses;
            this.averageIssuesPerAnalysis = averageIssuesPerAnalysis;
            this.highSeverityCount = highSeverityCount;
            this.mediumSeverityCount = mediumSeverityCount;
            this.lowSeverityCount = lowSeverityCount;
            this.mostProblematicFiles = mostProblematicFiles;
        }

        public long getTotalAnalyses() { return totalAnalyses; }
        public double getAverageIssuesPerAnalysis() { return averageIssuesPerAnalysis; }
        public long getHighSeverityCount() { return highSeverityCount; }
        public long getMediumSeverityCount() { return mediumSeverityCount; }
        public long getLowSeverityCount() { return lowSeverityCount; }
        public List<Object[]> getMostProblematicFiles() { return mostProblematicFiles; }
        public long getTotalIssues() { return highSeverityCount + mediumSeverityCount + lowSeverityCount; }
    }
}
