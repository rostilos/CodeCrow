package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;
import org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator;
import org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator.QualityGateResult;
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

    private final CodeAnalysisRepository codeAnalysisRepository;
    private final CodeAnalysisIssueRepository issueRepository;
    private final QualityGateRepository qualityGateRepository;
    private final QualityGateEvaluator qualityGateEvaluator;
    private static final Logger log = LoggerFactory.getLogger(CodeAnalysisService.class);

    @Autowired
    public CodeAnalysisService(
            CodeAnalysisRepository codeAnalysisRepository,
            CodeAnalysisIssueRepository issueRepository,
            QualityGateRepository qualityGateRepository
    ) {
        this.codeAnalysisRepository = codeAnalysisRepository;
        this.issueRepository = issueRepository;
        this.qualityGateRepository = qualityGateRepository;
        this.qualityGateEvaluator = new QualityGateEvaluator();
    }

    public CodeAnalysis createAnalysisFromAiResponse(
            Project project,
            Map<String, Object> analysisData,
            Long pullRequestId,
            String targetBranchName,
            String sourceBranchName,
            String commitHash,
            String vcsAuthorId,
            String vcsAuthorUsername
    ) {
        return createAnalysisFromAiResponse(project, analysisData, pullRequestId,
                targetBranchName, sourceBranchName, commitHash, vcsAuthorId, vcsAuthorUsername, null);
    }

    public CodeAnalysis createAnalysisFromAiResponse(
            Project project,
            Map<String, Object> analysisData,
            Long pullRequestId,
            String targetBranchName,
            String sourceBranchName,
            String commitHash,
            String vcsAuthorId,
            String vcsAuthorUsername,
            String diffFingerprint
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
            analysis.setDiffFingerprint(diffFingerprint);

            return fillAnalysisData(analysis, analysisData, commitHash, vcsAuthorId, vcsAuthorUsername);
        } catch (Exception e) {
            log.error("Error creating analysis from AI response: {}", e.getMessage(), e);
            throw new RuntimeException("Failed to create analysis from AI response", e);
        }
    }



    private CodeAnalysis fillAnalysisData(
            CodeAnalysis analysis,
            Map<String, Object> analysisData,
            String commitHash,
            String vcsAuthorId,
            String vcsAuthorUsername
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
                return codeAnalysisRepository.save(analysis);
            }

            // Save analysis first to get its ID for resolution tracking
            CodeAnalysis savedAnalysis = codeAnalysisRepository.save(analysis);
            Long analysisId = savedAnalysis.getId();
            Long prNumber = savedAnalysis.getPrNumber();

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
                        CodeAnalysisIssue issue = createIssueFromData(
                                issueData, String.valueOf(i), vcsAuthorId, vcsAuthorUsername,
                                commitHash, prNumber, analysisId);
                        if (issue != null) {
                            savedAnalysis.addIssue(issue);
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

                        CodeAnalysisIssue issue = createIssueFromData(
                                issueData, entry.getKey(), vcsAuthorId, vcsAuthorUsername,
                                commitHash, prNumber, analysisId);
                        if (issue != null) {
                            savedAnalysis.addIssue(issue);
                        }
                    } catch (Exception e) {
                        log.error("Error processing issue with key '{}': {}", entry.getKey(), e.getMessage(), e);
                    }
                }
            } else {
                log.warn("Issues field is neither List nor Map: {}", issuesObj.getClass().getName());
            }

            log.info("Successfully created analysis with {} issues", savedAnalysis.getIssues().size());
            
            // Evaluate quality gate — wrapped defensively so a QG failure
            // (e.g. detached entity, lazy-init) does not abort the entire analysis
            try {
                QualityGate qualityGate = getQualityGateForAnalysis(savedAnalysis);
                if (qualityGate != null) {
                    QualityGateResult qgResult = qualityGateEvaluator.evaluate(savedAnalysis, qualityGate);
                    savedAnalysis.setAnalysisResult(qgResult.result());
                    log.info("Quality gate '{}' evaluated with result: {}", qualityGate.getName(), qgResult.result());
                } else {
                    log.info("No quality gate found for analysis, skipping evaluation");
                }
            } catch (Exception qgEx) {
                log.warn("Quality gate evaluation failed, analysis will be saved without QG result: {}", qgEx.getMessage());
            }
            
            return codeAnalysisRepository.save(savedAnalysis);

        } catch (Exception e) {
            log.error("Error creating analysis from AI response: {}", e.getMessage(), e);
            throw new RuntimeException("Failed to create analysis from AI response", e);
        }
    }

    /**
     * Gets the quality gate for an analysis.
     * First checks if the project has a specific quality gate assigned,
     * otherwise falls back to the workspace default quality gate.
     */
    private QualityGate getQualityGateForAnalysis(CodeAnalysis analysis) {
        Project project = analysis.getProject();
        if (project == null) {
            log.warn("Analysis has no project, cannot determine quality gate");
            return null;
        }
        
        // First try project-specific quality gate
        QualityGate projectGate = project.getQualityGate();
        if (projectGate != null && projectGate.isActive()) {
            return projectGate;
        }
        
        // Fall back to workspace default quality gate
        Workspace workspace = project.getWorkspace();
        if (workspace == null) {
            log.warn("Project has no workspace, cannot determine default quality gate");
            return null;
        }
        
        return qualityGateRepository.findDefaultWithConditions(workspace.getId()).orElse(null);
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

    /**
     * Fallback cache lookup by commit hash only (ignoring PR number).
     * Handles close/reopen scenarios where the same commit gets a new PR number.
     */
    public Optional<CodeAnalysis> getAnalysisByCommitHash(Long projectId, String commitHash) {
        return codeAnalysisRepository.findTopByProjectIdAndCommitHash(projectId, commitHash);
    }

    /**
     * Content-based cache lookup by diff fingerprint.
     * Handles branch-cascade flows where the same code changes appear in different PRs
     * (e.g. feature→release analyzed, then release→main opens with the same changes).
     */
    public Optional<CodeAnalysis> getAnalysisByDiffFingerprint(Long projectId, String diffFingerprint) {
        if (diffFingerprint == null || diffFingerprint.isBlank()) {
            return Optional.empty();
        }
        return codeAnalysisRepository.findTopByProjectIdAndDiffFingerprint(projectId, diffFingerprint);
    }

    /**
     * Clone an existing analysis for a new PR.
     * Creates a new CodeAnalysis row with cloned issues, linked to the new PR identity.
     * Used when a fingerprint/commit-hash cache hit matches a different PR.
     *
     * // TODO: Option B — LIGHTWEIGHT mode: instead of full clone, reuse Stage 1 issues
     * //       but re-run Stage 2 cross-file analysis against the new target branch context.
     * //       This would catch interaction differences when target branches differ.
     *
     * // TODO: Consider tracking storage growth from cloned analyses. If it becomes significant,
     * //       explore referencing the original analysis instead of deep-copying issues.
     */
    public CodeAnalysis cloneAnalysisForPr(
            CodeAnalysis source,
            Project project,
            Long newPrNumber,
            String commitHash,
            String targetBranchName,
            String sourceBranchName,
            String diffFingerprint
    ) {
        // Guard against duplicates (same idempotency check as createAnalysisFromAiResponse)
        Optional<CodeAnalysis> existing = codeAnalysisRepository
                .findByProjectIdAndCommitHashAndPrNumber(project.getId(), commitHash, newPrNumber);
        if (existing.isPresent()) {
            log.info("Cloned analysis already exists for project={}, commit={}, pr={}. Returning existing.",
                    project.getId(), commitHash, newPrNumber);
            return existing.get();
        }

        int previousVersion = codeAnalysisRepository.findMaxPrVersion(project.getId(), newPrNumber).orElse(0);

        CodeAnalysis clone = new CodeAnalysis();
        clone.setProject(project);
        clone.setAnalysisType(source.getAnalysisType());
        clone.setPrNumber(newPrNumber);
        clone.setCommitHash(commitHash);
        clone.setDiffFingerprint(diffFingerprint);
        clone.setBranchName(targetBranchName);
        clone.setSourceBranchName(sourceBranchName);
        clone.setComment(source.getComment());
        clone.setStatus(source.getStatus());
        clone.setAnalysisResult(source.getAnalysisResult());
        clone.setPrVersion(previousVersion + 1);

        // Save first to get an ID
        CodeAnalysis saved = codeAnalysisRepository.save(clone);

        // Deep-copy issues
        for (CodeAnalysisIssue srcIssue : source.getIssues()) {
            CodeAnalysisIssue issueClone = new CodeAnalysisIssue();
            issueClone.setSeverity(srcIssue.getSeverity());
            issueClone.setFilePath(srcIssue.getFilePath());
            issueClone.setLineNumber(srcIssue.getLineNumber());
            issueClone.setReason(srcIssue.getReason());
            issueClone.setSuggestedFixDescription(srcIssue.getSuggestedFixDescription());
            issueClone.setSuggestedFixDiff(srcIssue.getSuggestedFixDiff());
            issueClone.setIssueCategory(srcIssue.getIssueCategory());
            issueClone.setResolved(srcIssue.isResolved());
            issueClone.setResolvedDescription(srcIssue.getResolvedDescription());
            issueClone.setVcsAuthorId(srcIssue.getVcsAuthorId());
            issueClone.setVcsAuthorUsername(srcIssue.getVcsAuthorUsername());
            saved.addIssue(issueClone);
        }

        saved.updateIssueCounts();
        CodeAnalysis result = codeAnalysisRepository.save(saved);
        log.info("Cloned analysis {} → {} for PR {} (fingerprint={}, {} issues)",
                source.getId(), result.getId(), newPrNumber,
                diffFingerprint != null ? diffFingerprint.substring(0, 8) + "..." : "null",
                result.getIssues().size());
        return result;
    }

    public Optional<CodeAnalysis> getPreviousVersionCodeAnalysis(Long projectId, Long prNumber) {
        return codeAnalysisRepository.findByProjectIdAndPrNumberWithMaxPrVersion(projectId, prNumber);
    }

    /**
     * Get all analyses for a PR across all versions.
     * Useful for providing full issue history to AI including resolved issues.
     */
    public List<CodeAnalysis> getAllPrAnalyses(Long projectId, Long prNumber) {
        return codeAnalysisRepository.findAllByProjectIdAndPrNumberOrderByPrVersionDesc(projectId, prNumber);
    }

    public int getMaxAnalysisPrVersion(Long projectId, Long prNumber) {
        return codeAnalysisRepository.findMaxPrVersion(projectId, prNumber).orElse(0);
    }

    public Optional<CodeAnalysis> findAnalysisByProjectAndPrNumberAndVersion(Long projectId, Long prNumber, int prVersion) {
        return codeAnalysisRepository.findByProjectIdAndPrNumberAndPrVersion(projectId, prNumber, prVersion);
    }

    private CodeAnalysisIssue createIssueFromData(
            Map<String, Object> issueData, 
            String issueKey, 
            String vcsAuthorId, 
            String vcsAuthorUsername,
            String commitHash,
            Long prNumber,
            Long analysisId
    ) {
        try {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();

            issue.setVcsAuthorId(vcsAuthorId);
            issue.setVcsAuthorUsername(vcsAuthorUsername);

            // Check if this is a persisted issue from previous analysis (has original ID)
            Object originalIdObj = issueData.get("id");
            CodeAnalysisIssue originalIssue = null;
            if (originalIdObj != null) {
                try {
                    Long originalId = null;
                    if (originalIdObj instanceof String) {
                        originalId = Long.parseLong((String) originalIdObj);
                    } else if (originalIdObj instanceof Number) {
                        originalId = ((Number) originalIdObj).longValue();
                    }
                    if (originalId != null) {
                        originalIssue = issueRepository.findById(originalId).orElse(null);
                        if (originalIssue != null) {
                            log.debug("Found original issue {} for reconciliation", originalId);
                        }
                    }
                } catch (NumberFormatException e) {
                    log.debug("Could not parse issue ID '{}' as Long, treating as new issue", originalIdObj);
                }
            }

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

            // Parse isResolved - handle both Boolean and String representations
            Object isResolvedObj = issueData.get("isResolved");
            boolean isResolved = false;
            if (isResolvedObj instanceof Boolean) {
                isResolved = (Boolean) isResolvedObj;
            } else if (isResolvedObj instanceof String) {
                isResolved = "true".equalsIgnoreCase((String) isResolvedObj);
            }
            issue.setResolved(isResolved);
            
            log.debug("Issue resolved status: isResolvedObj={}, type={}, parsed={}", 
                    isResolvedObj, isResolvedObj != null ? isResolvedObj.getClass().getSimpleName() : "null", isResolved);

            // If this issue is resolved and we have original issue data, populate resolution tracking
            if (isResolved && originalIssue != null) {
                issue.setResolvedDescription(reason); // AI provides resolution reason in the 'reason' field
                issue.setResolvedByPr(prNumber);
                issue.setResolvedCommitHash(commitHash);
                issue.setResolvedAnalysisId(analysisId);
                issue.setResolvedAt(OffsetDateTime.now());
                issue.setResolvedBy(vcsAuthorUsername);
                log.info("Issue {} marked as resolved by PR {} commit {}", originalIdObj, prNumber, commitHash);
            }

            String categoryStr = (String) issueData.get("category");
            if (categoryStr != null && !categoryStr.isBlank()) {
                issue.setIssueCategory(IssueCategory.fromString(categoryStr));
            } else {
                issue.setIssueCategory(IssueCategory.CODE_QUALITY);
            }

            log.debug("Created issue: {} severity, category: {}, file: {}, line: {}, resolved: {}",
                    issue.getSeverity(), issue.getIssueCategory(), issue.getFilePath(), issue.getLineNumber(), isResolved);

            return issue;

        } catch (Exception e) {
            log.error("Error creating issue from data for key '{}': {}", issueKey, e.getMessage(), e);
            return null;
        }
    }

    /**
     * Overload for backward compatibility with callers that don't have resolution context
     */
    private CodeAnalysisIssue createIssueFromData(Map<String, Object> issueData, String issueKey, String vcsAuthorId, String vcsAuthorUsername) {
        return createIssueFromData(issueData, issueKey, vcsAuthorId, vcsAuthorUsername, null, null, null);
    }

    public CodeAnalysis createAnalysis(Project project, AnalysisType analysisType) {
        CodeAnalysis analysis = new CodeAnalysis();
        analysis.setProject(project);
        analysis.setAnalysisType(analysisType);
        analysis.setStatus(AnalysisStatus.PENDING);
        return codeAnalysisRepository.save(analysis);
    }

    public CodeAnalysis saveAnalysis(CodeAnalysis analysis) {
        analysis.updateIssueCounts();
        return codeAnalysisRepository.save(analysis);
    }

    public Optional<CodeAnalysis> findById(Long id) {
        return codeAnalysisRepository.findById(id);
    }

    public List<CodeAnalysis> findByProjectId(Long projectId) {
        return codeAnalysisRepository.findByProjectIdOrderByCreatedAtDesc(projectId);
    }

    public List<CodeAnalysis> findByProjectIdAndType(Long projectId, AnalysisType analysisType) {
        return codeAnalysisRepository.findByProjectIdAndAnalysisTypeOrderByCreatedAtDesc(projectId, analysisType);
    }

    public Optional<CodeAnalysis> findByProjectIdAndPrNumber(Long projectId, Long prNumber) {
        return codeAnalysisRepository.findByProjectIdAndPrNumber(projectId, prNumber);
    }

    public Optional<CodeAnalysis> findByProjectIdAndPrNumberAndPrVersion(Long projectId, Long prNumber, int prVersion) {
        return codeAnalysisRepository.findByProjectIdAndPrNumberAndPrVersion(projectId, prNumber, prVersion);
    }

    public List<CodeAnalysis> findByProjectIdAndDateRange(Long projectId, OffsetDateTime startDate, OffsetDateTime endDate) {
        return codeAnalysisRepository.findByProjectIdAndDateRange(projectId, startDate, endDate);
    }

    public List<CodeAnalysis> findByProjectIdWithHighSeverityIssues(Long projectId) {
        return codeAnalysisRepository.findByProjectIdWithHighSeverityIssues(projectId);
    }

    /**
     * Paginated search for analyses with optional filters.
     * Filters and pagination are handled at the database level for better performance.
     *
     * @param projectId the project ID (required)
     * @param prNumber optional PR number filter
     * @param status optional status filter
     * @param pageable pagination parameters
     * @return a page of analyses matching the criteria
     */
    public org.springframework.data.domain.Page<CodeAnalysis> searchAnalyses(
            Long projectId,
            Long prNumber,
            AnalysisStatus status,
            org.springframework.data.domain.Pageable pageable) {
        return codeAnalysisRepository.searchAnalyses(projectId, prNumber, status, pageable);
    }

    public Optional<CodeAnalysis> findLatestByProjectId(Long projectId) {
        return codeAnalysisRepository.findLatestByProjectId(projectId);
    }

    public AnalysisStats getProjectAnalysisStats(Long projectId) {
        long totalAnalyses = codeAnalysisRepository.countByProjectId(projectId);
        Double avgIssues = codeAnalysisRepository.getAverageIssuesPerAnalysis(projectId);

        long highSeverityCount = issueRepository.countByProjectIdAndSeverity(projectId, IssueSeverity.HIGH);
        long mediumSeverityCount = issueRepository.countByProjectIdAndSeverity(projectId, IssueSeverity.MEDIUM);
        long lowSeverityCount = issueRepository.countByProjectIdAndSeverity(projectId, IssueSeverity.LOW);
        long infoSeverityCount = issueRepository.countByProjectIdAndSeverity(projectId, IssueSeverity.INFO);

        List<Object[]> problematicFiles = issueRepository.findMostProblematicFilesByProjectId(projectId);

        return new AnalysisStats(totalAnalyses, avgIssues != null ? avgIssues : 0.0,
                highSeverityCount, mediumSeverityCount, lowSeverityCount, infoSeverityCount, problematicFiles);
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
        codeAnalysisRepository.deleteById(analysisId);
    }

    public void deleteAllAnalysesByProjectId(Long projectId) {
        codeAnalysisRepository.deleteByProjectId(projectId);
    }

    public static class AnalysisStats {
        private final long totalAnalyses;
        private final double averageIssuesPerAnalysis;
        private final long highSeverityCount;
        private final long mediumSeverityCount;
        private final long lowSeverityCount;
        private final long infoSeverityCount;
        private final List<Object[]> mostProblematicFiles;

        public AnalysisStats(long totalAnalyses, double averageIssuesPerAnalysis,
                             long highSeverityCount, long mediumSeverityCount, long lowSeverityCount,
                             long infoSeverityCount, List<Object[]> mostProblematicFiles) {
            this.totalAnalyses = totalAnalyses;
            this.averageIssuesPerAnalysis = averageIssuesPerAnalysis;
            this.highSeverityCount = highSeverityCount;
            this.mediumSeverityCount = mediumSeverityCount;
            this.lowSeverityCount = lowSeverityCount;
            this.infoSeverityCount = infoSeverityCount;
            this.mostProblematicFiles = mostProblematicFiles;
        }

        public long getTotalAnalyses() { return totalAnalyses; }
        public double getAverageIssuesPerAnalysis() { return averageIssuesPerAnalysis; }
        public long getHighSeverityCount() { return highSeverityCount; }
        public long getMediumSeverityCount() { return mediumSeverityCount; }
        public long getLowSeverityCount() { return lowSeverityCount; }
        public long getInfoSeverityCount() { return infoSeverityCount; }
        public List<Object[]> getMostProblematicFiles() { return mostProblematicFiles; }
        public long getTotalIssues() { return highSeverityCount + mediumSeverityCount + lowSeverityCount + infoSeverityCount; }
    }
}
