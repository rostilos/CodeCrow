package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.qualitygate.QualityGate;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;
import org.rostilos.codecrow.core.model.qualitygate.QualityGateResult;
import org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator;
import org.rostilos.codecrow.core.util.tracking.DiffSanitizer;
import org.rostilos.codecrow.core.util.tracking.IssueFingerprint;
import org.rostilos.codecrow.core.util.anchoring.SnippetAnchoringService;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.*;

@Service
@Transactional
public class CodeAnalysisService {

    private final CodeAnalysisRepository codeAnalysisRepository;
    private final CodeAnalysisIssueRepository issueRepository;
    private final QualityGateRepository qualityGateRepository;
    private final QualityGateEvaluator qualityGateEvaluator;
    private final IssueDeduplicationService issueDeduplicationService;
    private static final Logger log = LoggerFactory.getLogger(CodeAnalysisService.class);

    @Autowired
    public CodeAnalysisService(
            CodeAnalysisRepository codeAnalysisRepository,
            CodeAnalysisIssueRepository issueRepository,
            QualityGateRepository qualityGateRepository,
            QualityGateEvaluator qualityGateEvaluator,
            IssueDeduplicationService issueDeduplicationService
    ) {
        this.codeAnalysisRepository = codeAnalysisRepository;
        this.issueRepository = issueRepository;
        this.qualityGateRepository = qualityGateRepository;
        this.qualityGateEvaluator = qualityGateEvaluator;
        this.issueDeduplicationService = issueDeduplicationService;
    }

    /**
     * Backward-compatible short overload — no diff fingerprint, no file contents.
     */
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
                targetBranchName, sourceBranchName, commitHash, vcsAuthorId, vcsAuthorUsername,
                null, Collections.emptyMap());
    }

    /**
     * Overload with diff fingerprint but no file contents.
     */
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
        return createAnalysisFromAiResponse(project, analysisData, pullRequestId,
                targetBranchName, sourceBranchName, commitHash, vcsAuthorId, vcsAuthorUsername,
                diffFingerprint, Collections.emptyMap());
    }

    /**
     * Full overload with explicit file contents for line hash computation.
     *
     * @param fileContents map of filePath → raw file content for line hash computation.
     *                     If empty, fingerprints will be computed without line-hash anchoring.
     */
    public CodeAnalysis createAnalysisFromAiResponse(
            Project project,
            Map<String, Object> analysisData,
            Long pullRequestId,
            String targetBranchName,
            String sourceBranchName,
            String commitHash,
            String vcsAuthorId,
            String vcsAuthorUsername,
            String diffFingerprint,
            Map<String, String> fileContents
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

            return fillAnalysisData(analysis, analysisData, commitHash, vcsAuthorId, vcsAuthorUsername,
                    fileContents != null ? fileContents : Collections.emptyMap());
        } catch (Exception e) {
            log.error("Error creating analysis from AI response: {}", e.getMessage(), e);
            throw new RuntimeException("Failed to create analysis from AI response", e);
        }
    }

    /**
     * Creates a CodeAnalysis record from AI response for a direct push (hybrid branch analysis).
     * Unlike {@link #createAnalysisFromAiResponse}, this method:
     * <ul>
     *   <li>Sets {@link AnalysisType#BRANCH_ANALYSIS} instead of PR_REVIEW</li>
     *   <li>Does NOT require a PR number (null prNumber)</li>
     *   <li>Marks all issues with {@link DetectionSource#DIRECT_PUSH_ANALYSIS}</li>
     *   <li>Uses commit hash + analysis type for idempotency (not commit + prNumber)</li>
     * </ul>
     *
     * @param project         the project owning the analysis
     * @param analysisData    the parsed AI response map
     * @param targetBranchName the branch where commits were pushed
     * @param commitHash      the HEAD commit hash of the analyzed range
     * @param fileContents    map of filePath → raw file content for line hash computation
     * @return the persisted CodeAnalysis with issues marked as DIRECT_PUSH_ANALYSIS
     */
    public CodeAnalysis createDirectPushAnalysisFromAiResponse(
            Project project,
            Map<String, Object> analysisData,
            String targetBranchName,
            String commitHash,
            Map<String, String> fileContents
    ) {
        try {
            // Idempotency: check if a direct push analysis already exists for this commit
            Optional<CodeAnalysis> existingAnalysis = codeAnalysisRepository
                    .findByProjectIdAndCommitHashAndAnalysisType(
                            project.getId(), commitHash, AnalysisType.BRANCH_ANALYSIS);

            if (existingAnalysis.isPresent()) {
                log.info("Direct push analysis already exists for project={}, commit={}. Returning existing.",
                        project.getId(), commitHash);
                return existingAnalysis.get();
            }

            CodeAnalysis analysis = new CodeAnalysis();
            analysis.setProject(project);
            analysis.setAnalysisType(AnalysisType.BRANCH_ANALYSIS);
            analysis.setPrNumber(null); // No PR for direct push
            analysis.setBranchName(targetBranchName);
            analysis.setSourceBranchName(null); // Direct push has no source branch
            analysis.setPrVersion(0); // Not versioned like PR analyses

            CodeAnalysis savedAnalysis = fillAnalysisData(analysis, analysisData, commitHash,
                    null, null, // No VCS author info for direct push analysis
                    fileContents != null ? fileContents : Collections.emptyMap());

            // Mark all issues as detected via direct push analysis (not PR)
            for (CodeAnalysisIssue issue : savedAnalysis.getIssues()) {
                issue.setDetectionSource(DetectionSource.DIRECT_PUSH_ANALYSIS);
            }

            log.info("Created direct push analysis for project={}, branch={}, commit={}, {} issues",
                    project.getId(), targetBranchName, commitHash, savedAnalysis.getIssues().size());

            return codeAnalysisRepository.save(savedAnalysis);
        } catch (Exception e) {
            log.error("Error creating direct push analysis: project={}, commit={}: {}",
                    project.getId(), commitHash, e.getMessage(), e);
            throw new RuntimeException("Failed to create direct push analysis", e);
        }
    }

    /**
     * Populates the analysis with issues from the AI response data.
     *
     * @param fileContents map of filePath → raw file content for line hash computation
     */
    private CodeAnalysis fillAnalysisData(
            CodeAnalysis analysis,
            Map<String, Object> analysisData,
            String commitHash,
            String vcsAuthorId,
            String vcsAuthorUsername,
            Map<String, String> fileContents
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
                                commitHash, prNumber, analysisId, fileContents);
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
                                commitHash, prNumber, analysisId, fileContents);
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

            int rawIssueCount = savedAnalysis.getIssues().size();
            log.info("Created {} issues from AI response, applying Java-side post-processing...", rawIssueCount);

            // Count Java-side processing stats for validation logging
            long linesCorrected = savedAnalysis.getIssues().stream()
                    .filter(i -> i.getLineHash() != null)
                    .count();
            long diffsRestored = savedAnalysis.getIssues().stream()
                    .filter(i -> i.getSuggestedFixDiff() != null
                            && !DiffSanitizer.NO_FIX_PLACEHOLDER.equals(i.getSuggestedFixDiff()))
                    .count();

            // De-duplicate issues at ingestion time (3-tier: structural, whole-file wildcard, fingerprint)
            List<CodeAnalysisIssue> deduplicated = issueDeduplicationService.deduplicateAtIngestion(
                    savedAnalysis.getIssues());
            int deduped = rawIssueCount - deduplicated.size();
            if (deduped > 0) {
                savedAnalysis.getIssues().clear();
                for (CodeAnalysisIssue issue : deduplicated) {
                    savedAnalysis.addIssue(issue);
                }
            }

            log.info("Java post-processing complete: {} raw → {} final issues "
                            + "(deduped={}, linesHashed={}, diffsPresent={})",
                    rawIssueCount, savedAnalysis.getIssues().size(),
                    deduped, linesCorrected, diffsRestored);
            
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
        clone.setClonedFromAnalysisId(source.getId());

        // Save first to get an ID
        CodeAnalysis saved = codeAnalysisRepository.save(clone);

        // Deep-copy issues
        for (CodeAnalysisIssue srcIssue : source.getIssues()) {
            CodeAnalysisIssue issueClone = new CodeAnalysisIssue();
            issueClone.setSeverity(srcIssue.getSeverity());
            issueClone.setFilePath(srcIssue.getFilePath());
            issueClone.setLineNumber(srcIssue.getLineNumber());
            issueClone.setReason(srcIssue.getReason());
            issueClone.setTitle(srcIssue.getTitle());
            issueClone.setSuggestedFixDescription(srcIssue.getSuggestedFixDescription());
            issueClone.setSuggestedFixDiff(srcIssue.getSuggestedFixDiff());
            issueClone.setIssueCategory(srcIssue.getIssueCategory());
            issueClone.setResolved(srcIssue.isResolved());
            issueClone.setResolvedDescription(srcIssue.getResolvedDescription());
            issueClone.setVcsAuthorId(srcIssue.getVcsAuthorId());
            issueClone.setVcsAuthorUsername(srcIssue.getVcsAuthorUsername());
            // Copy content-based tracking hashes
            issueClone.setLineHash(srcIssue.getLineHash());
            issueClone.setLineHashContext(srcIssue.getLineHashContext());
            issueClone.setIssueFingerprint(srcIssue.getIssueFingerprint());
            issueClone.setContentFingerprint(srcIssue.getContentFingerprint());
            issueClone.setCodeSnippet(srcIssue.getCodeSnippet());
            // Copy PR tracking lineage
            issueClone.setTrackedFromIssueId(srcIssue.getTrackedFromIssueId());
            issueClone.setTrackingConfidence(srcIssue.getTrackingConfidence());
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
            Long analysisId,
            Map<String, String> fileContents
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

            // Set line number — handles integers, stringified integers, and range strings like "42-45"
            Object lineObj = issueData.get("line");
            if (lineObj != null) {
                try {
                    if (lineObj instanceof Number) {
                        issue.setLineNumber(((Number) lineObj).intValue());
                    } else if (lineObj instanceof String) {
                        String lineStr = ((String) lineObj).trim();
                        // Handle range format "42-45" → take the start line
                        int dashIdx = lineStr.indexOf('-');
                        if (dashIdx > 0) {
                            lineStr = lineStr.substring(0, dashIdx).trim();
                        }
                        issue.setLineNumber(Integer.parseInt(lineStr));
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

            // Set title (short label for the issue)
            String title = (String) issueData.get("title");
            if (title == null || title.isBlank()) {
                // Fallback: derive title from reason — first sentence or truncated
                int sentenceEnd = reason.indexOf(". ");
                if (sentenceEnd > 0 && sentenceEnd <= 120) {
                    title = reason.substring(0, sentenceEnd);
                } else {
                    title = reason.length() > 120 ? reason.substring(0, 117) + "..." : reason;
                }
            } else if (title.length() > 255) {
                title = title.substring(0, 252) + "...";
            }
            issue.setTitle(title);

            String suggestedFixDescription = (String) issueData.get("suggestedFixDescription");
            if (suggestedFixDescription == null) {
                suggestedFixDescription = "No suggested fix description provided";
            }
            issue.setSuggestedFixDescription(suggestedFixDescription);


            String suggestedFixDiff = (String) issueData.get("suggestedFixDiff");
            if (suggestedFixDiff == null) {
                suggestedFixDiff = "No suggested fix provided";
            }
            // Sanitize: strip markdown code-block fences (```diff / ```)
            suggestedFixDiff = DiffSanitizer.cleanDiffFormat(suggestedFixDiff);
            issue.setSuggestedFixDiff(suggestedFixDiff);

            // Restore missing diff/description from the original issue if this is a
            // persisted issue whose LLM re-emission dropped the fix suggestion.
            if (originalIssue != null) {
                boolean diffMissing = suggestedFixDiff == null
                        || DiffSanitizer.NO_FIX_PLACEHOLDER.equals(suggestedFixDiff)
                        || suggestedFixDiff.strip().length() < 10;
                if (diffMissing) {
                    String origDiff = originalIssue.getSuggestedFixDiff();
                    if (origDiff != null && !DiffSanitizer.NO_FIX_PLACEHOLDER.equals(origDiff)
                            && origDiff.strip().length() >= 10) {
                        issue.setSuggestedFixDiff(DiffSanitizer.cleanDiffFormat(origDiff));
                        log.info("Restored suggestedFixDiff from original issue {} for persisting issue",
                                originalIssue.getId());
                    }
                }

                boolean descMissing = "No suggested fix description provided".equals(suggestedFixDescription)
                        || suggestedFixDescription == null || suggestedFixDescription.isBlank();
                if (descMissing) {
                    String origDesc = originalIssue.getSuggestedFixDescription();
                    if (origDesc != null && !origDesc.isBlank()
                            && !"No suggested fix description provided".equals(origDesc)) {
                        issue.setSuggestedFixDescription(origDesc);
                        log.debug("Restored suggestedFixDescription from original issue {}",
                                originalIssue.getId());
                    }
                }
            }

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
                // Prefer dedicated resolutionReason field; fall back to generic text.
                // Do NOT use 'reason' — that is the issue description, not the resolution explanation.
                String resolutionReason = (String) issueData.get("resolutionReason");
                if (resolutionReason == null || resolutionReason.isBlank()) {
                    resolutionReason = "Resolved in PR review iteration";
                }
                issue.setResolvedDescription(resolutionReason);
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

            // ── Parse issue scope (LINE, BLOCK, FUNCTION, FILE) ──
            String scopeStr = (String) issueData.get("scope");
            if (scopeStr != null && !scopeStr.isBlank()) {
                issue.setIssueScope(IssueScope.fromString(scopeStr));
            }
            // Auto-infer scope for unanchored issues: line <= 1 with no snippet → FILE
            // This will be refined after codeSnippet is parsed below

            // NOTE: endLineNumber is no longer parsed from the AI response.
            // The AST parser sets scope boundaries (endLineNumber) after snippet anchoring.

            // --- Compute content-based tracking hashes ---
            // Extract codeSnippet (the exact source line the LLM references) for
            // content-based line anchoring. computeTrackingHashes will use it to
            // verify/correct the LLM-reported line number against actual file content.
            String codeSnippet = (String) issueData.get("codeSnippet");

            // ── Handle unanchored issues (no real codeSnippet) ──
            // The LLM sometimes reports architectural/style observations at line 1
            // without providing a specific code reference. These issues cannot be
            // anchored to any real code line. Set lineNumber to 1 so they still
            // display on VCS platforms and the source viewer. They're identified
            // as "unanchored" by having line <= 1 AND no codeSnippet.
            if ((issue.getLineNumber() == null || issue.getLineNumber() <= 1)
                    && (codeSnippet == null || codeSnippet.isBlank())) {
                issue.setLineNumber(1);
                log.debug("Unanchored issue (line<={}, no codeSnippet): file={}, title={}. Set to line=1 for display.",
                        issue.getLineNumber(), issue.getFilePath(), issue.getTitle());
            }

            // Persist the snippet so it's available for re-anchoring at every later
            // stage: branch reconciliation, IssueTracker, and serve-time correction.
            if (codeSnippet != null && !codeSnippet.isBlank()) {
                issue.setCodeSnippet(codeSnippet);
            } else {
                log.warn("AI returned issue without codeSnippet: file={}, line={}, title={}. "
                        + "Content-based line anchoring disabled for this issue.",
                        issue.getFilePath(), issue.getLineNumber(), issue.getTitle());
            }

            // ── Auto-infer / correct scope ──
            if (issue.getIssueScope() == null) {
                if ((issue.getLineNumber() == null || issue.getLineNumber() <= 1)
                        && (codeSnippet == null || codeSnippet.isBlank())) {
                    issue.setIssueScope(IssueScope.FILE);
                } else {
                    issue.setIssueScope(IssueScope.LINE);
                }
            } else if (issue.getIssueScope() == IssueScope.LINE
                    && (issue.getLineNumber() == null || issue.getLineNumber() <= 1)
                    && (codeSnippet == null || codeSnippet.isBlank())) {
                // AI said LINE but the issue has no real anchor — override to FILE.
                // LINE means "affects a single line" which is contradictory when there
                // is no specific line reference (no codeSnippet, line ≤ 1).
                issue.setIssueScope(IssueScope.FILE);
                log.debug("Overrode AI scope LINE → FILE for unanchored issue: file={}, title={}",
                        issue.getFilePath(), issue.getTitle());
            }

            // ── Server-side snippet anchoring ──
            // The LLM's line number is a best-effort hint (it only sees diffs, not full files).
            // Use the codeSnippet to find the actual line in the real file content.
            // Scope boundaries are NOT resolved here — instead, scope-aware context
            // hashing in computeTrackingHashes() captures the surrounding code window.
            if (codeSnippet != null && !codeSnippet.isBlank() && fileContents != null && filePath != null) {
                String fileContent = fileContents.get(filePath);
                if (fileContent != null && !fileContent.isEmpty()) {
                    try {
                        SnippetAnchoringService.AnchorResult anchor = SnippetAnchoringService.anchor(
                                codeSnippet, fileContent,
                                issue.getLineNumber() != null ? issue.getLineNumber() : 1,
                                filePath);

                        if (anchor.shouldOverrideLine()) {
                            int oldLine = issue.getLineNumber() != null ? issue.getLineNumber() : 0;
                            issue.setLineNumber(anchor.startLine());

                            if (oldLine != anchor.startLine()) {
                                log.info("Snippet anchoring corrected {}:{} → {} (strategy={}, confidence={})",
                                        filePath, oldLine, anchor.startLine(),
                                        anchor.matchStrategy(), anchor.confidence());
                            }
                        }
                    } catch (Exception e) {
                        log.warn("Snippet anchoring failed for {}:{}: {}",
                                filePath, issue.getLineNumber(), e.getMessage());
                    }
                }
            }

            computeTrackingHashes(issue, fileContents, codeSnippet);

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
        return createIssueFromData(issueData, issueKey, vcsAuthorId, vcsAuthorUsername, null, null, null, Collections.emptyMap());
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

    public Optional<CodeAnalysis> findLatestByProjectIdAndBranch(Long projectId, String branchName) {
        return codeAnalysisRepository.findLatestByProjectIdAndBranchName(projectId, branchName);
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

    /**
     * Find all issues for a file across all analyses on a branch.
     * Deduplicates tracked issues, keeping only the version from the most recent analysis.
     */
    public List<CodeAnalysisIssue> findIssuesByBranchAndFilePath(Long projectId, String branchName, String filePath) {
        return deduplicateBranchIssues(
                issueRepository.findByProjectIdAndBranchNameAndFilePath(projectId, branchName, filePath));
    }

    /**
     * Find all issues across all analyses on a branch.
     * Deduplicates tracked issues, keeping only the version from the most recent analysis.
     */
    public List<CodeAnalysisIssue> findIssuesByBranch(Long projectId, String branchName) {
        return deduplicateBranchIssues(
                issueRepository.findByProjectIdAndBranchName(projectId, branchName));
    }

    /**
     * Find all issues across all analyses for a specific PR number.
     * Deduplicates tracked issues, keeping only the version from the most recent analysis.
     */
    public List<CodeAnalysisIssue> findIssuesByPrNumber(Long projectId, Long prNumber) {
        return deduplicateBranchIssues(
                issueRepository.findByProjectIdAndPrNumber(projectId, prNumber));
    }

    /**
     * Find all issues for a specific file across all analyses for a PR number.
     * Deduplicates tracked issues, keeping only the version from the most recent analysis.
     */
    public List<CodeAnalysisIssue> findIssuesByPrNumberAndFilePath(Long projectId, Long prNumber, String filePath) {
        return deduplicateBranchIssues(
                issueRepository.findByProjectIdAndPrNumberAndFilePath(projectId, prNumber, filePath));
    }

    /**
     * Deduplicate issues that span multiple analyses on the same branch.
     * When the same logical issue is tracked across analyses (via trackedFromIssueId),
     * we keep only the most recent version (highest analysis ID).
     * Issues with the same fingerprint are also deduplicated.
     * <p>
     * Uses a two-pass fingerprint strategy:
     * <ol>
     *   <li>Primary: issueFingerprint (category + lineHash + normalizedTitle)</li>
     *   <li>Secondary: contentFingerprint (lineHash + normalizedTitle only) — catches
     *       the same issue classified as STYLE in one PR and CODE_QUALITY in another</li>
     * </ol>
     */
    private List<CodeAnalysisIssue> deduplicateBranchIssues(List<CodeAnalysisIssue> allIssues) {
        if (allIssues == null || allIssues.isEmpty()) return List.of();

        // Build a set of issue IDs that were tracked forward (superseded by a newer version)
        Set<Long> supersededIds = new HashSet<>();
        for (CodeAnalysisIssue issue : allIssues) {
            if (issue.getTrackedFromIssueId() != null) {
                supersededIds.add(issue.getTrackedFromIssueId());
            }
        }

        // Pass 1: deduplicate by exact fingerprint (category-aware)
        Map<String, CodeAnalysisIssue> byFingerprint = new LinkedHashMap<>();
        for (CodeAnalysisIssue issue : allIssues) {
            if (supersededIds.contains(issue.getId())) {
                continue; // skip issues that were tracked forward to a newer analysis
            }
            String fp = issue.getIssueFingerprint();
            if (fp != null && !fp.isEmpty()) {
                CodeAnalysisIssue existing = byFingerprint.get(fp);
                if (existing == null || issue.getAnalysis().getId() > existing.getAnalysis().getId()) {
                    byFingerprint.put(fp, issue);
                }
            } else {
                // No fingerprint — include it (keyed by id to avoid duplicates)
                byFingerprint.put("_id_" + issue.getId(), issue);
            }
        }

        // Pass 2: deduplicate survivors by content fingerprint (category-agnostic)
        // This catches the same issue classified as STYLE vs CODE_QUALITY across PRs
        Map<String, CodeAnalysisIssue> byContentFp = new LinkedHashMap<>();
        for (CodeAnalysisIssue issue : byFingerprint.values()) {
            String cfp = issue.getContentFingerprint();
            if (cfp != null && !cfp.isEmpty()) {
                CodeAnalysisIssue existing = byContentFp.get(cfp);
                if (existing == null || issue.getAnalysis().getId() > existing.getAnalysis().getId()) {
                    byContentFp.put(cfp, issue);
                }
            } else {
                byContentFp.put("_id_" + issue.getId(), issue);
            }
        }

        return new ArrayList<>(byContentFp.values());
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

    /**
     * Compute line hash, context hash, and issue fingerprint for a newly created issue.
     * <p>
     * If a {@code codeSnippet} is provided (the exact source line the LLM references),
     * it is used to verify and correct the LLM-reported line number against the actual
     * file content. The snippet is hashed and looked up in the {@link LineHashSequence}
     * reverse index to find where that content actually lives in the file, then the
     * closest match to the reported line is chosen.
     *
     * @param issue        the issue to compute hashes for
     * @param fileContents map of filePath → raw file content; may be empty but not null
     * @param codeSnippet  the exact source line from the LLM's codeSnippet field; may be null
     */
    private void computeTrackingHashes(CodeAnalysisIssue issue, Map<String, String> fileContents, String codeSnippet) {
        try {
            String filePath = issue.getFilePath();
            Integer lineNumber = issue.getLineNumber();

            LineHashSequence lineHashes = LineHashSequence.empty();

            if (fileContents != null && filePath != null && fileContents.containsKey(filePath)) {
                lineHashes = LineHashSequence.from(fileContents.get(filePath));
            }

            // ── Content-based line correction using codeSnippet ──
            // If the LLM provided a codeSnippet, hash it and look up the actual line(s)
            // in the file that match. Pick the closest to the reported line.
            // Handle multi-line snippets: the AI often returns multi-line code blocks.
            // Check each line individually and pick the best (closest) match.
            if (codeSnippet != null && !codeSnippet.isBlank()
                    && lineNumber != null && lineNumber > 0
                    && lineHashes.getLineCount() > 0) {

                int correctedLine = -1;
                String[] snippetLines = codeSnippet.split("\\r?\\n");
                int bestDist = Integer.MAX_VALUE;

                for (String snippetLine : snippetLines) {
                    if (snippetLine == null || snippetLine.isBlank()) continue;
                    String snippetLineHash = LineHashSequence.hashLine(snippetLine);
                    int foundLine = lineHashes.findClosestLineForHash(snippetLineHash, lineNumber);
                    if (foundLine > 0) {
                        int dist = Math.abs(foundLine - lineNumber);
                        if (dist < bestDist) {
                            bestDist = dist;
                            correctedLine = foundLine;
                        }
                    }
                }

                if (correctedLine > 0 && correctedLine != lineNumber) {
                    log.info("Content-match corrected line for {}:{} -> {} (snippet: \"{}\")",
                            filePath, lineNumber, correctedLine,
                            codeSnippet.length() > 80 ? codeSnippet.substring(0, 77) + "..." : codeSnippet);
                    issue.setLineNumber(correctedLine);
                    lineNumber = correctedLine;
                } else if (correctedLine == lineNumber) {
                    log.debug("Content-match confirmed line {}:{} (snippet matches)", filePath, lineNumber);
                } else {
                    log.debug("Content-match found no match for snippet at {}:{}, keeping reported line",
                            filePath, lineNumber);
                }
            }

            String lineHash = null;
            String contextHash = null;

            // Skip lineHash computation when line <= 1 AND there's no codeSnippet.
            // Line 1 is typically a display placeholder for unanchored issues. Using
            // line 1's hash as a content anchor makes the issue "immortal" because
            // the deterministic tracker always finds boilerplate on line 1 unchanged.
            // These issues must be reconciled via AI instead of content hashing.
            boolean hasReliableLineAnchor = lineNumber != null && lineNumber > 1;
            boolean hasSnippetAnchor = codeSnippet != null && !codeSnippet.isBlank();

            if ((hasReliableLineAnchor || hasSnippetAnchor)
                    && lineNumber != null && lineNumber > 0
                    && lineHashes.getLineCount() > 0) {
                lineHash = lineHashes.getHashForLine(lineNumber);
                contextHash = computeContextHash(lineHashes, lineNumber,
                        issue.getEndLineNumber(), codeSnippet);
            } else if (lineNumber != null && lineNumber <= 1 && !hasSnippetAnchor) {
                log.debug("Skipping lineHash for {}:{} — unanchored issue (line<=1, no codeSnippet) must be reconciled via AI",
                        filePath, lineNumber);
            }

            issue.setLineHash(lineHash);
            issue.setLineHashContext(contextHash);

            // ── Scope-aware fingerprinting ──
            // FILE-scope issues intentionally use "no_hash" for the lineHash component
            // (even if we computed one above) so their fingerprint stays stable regardless
            // of which line they're displayed on. This prevents the deterministic tracker
            // from treating them as "immortal" via line-1 hash matching.
            String fingerprintLineHash = lineHash;
            if (issue.getIssueScope() == IssueScope.FILE) {
                fingerprintLineHash = null; // IssueFingerprint.compute treats null as "no_hash"
            }

            String fingerprint = IssueFingerprint.compute(
                    issue.getIssueCategory(),
                    fingerprintLineHash,
                    issue.getTitle()
            );
            issue.setIssueFingerprint(fingerprint);

            // Compute category-agnostic content fingerprint for cross-PR dedup.
            // Resilient to AI classifying the same issue as STYLE vs CODE_QUALITY etc.
            String contentFp = IssueFingerprint.computeContentFingerprint(fingerprintLineHash, issue.getTitle());
            issue.setContentFingerprint(contentFp);

            log.debug("Computed tracking hashes for issue at {}:{} — lineHash={}, fingerprint={}, contentFp={}",
                    filePath, lineNumber,
                    lineHash != null ? lineHash.substring(0, 8) + "..." : "null",
                    fingerprint.substring(0, 8) + "...",
                    contentFp.substring(0, 8) + "...");
        } catch (Exception e) {
            log.warn("Failed to compute tracking hashes for issue at {}:{}: {}",
                    issue.getFilePath(), issue.getLineNumber(), e.getMessage());
        }
    }

    /**
     * Compute the context hash for an issue based on its <em>actual</em> data,
     * not hardcoded radius values.
     * <p>
     * The context window is derived from the real scope boundaries:
     * <ol>
     *   <li>If {@code endLine} is known → hash the exact range {@code [lineNumber, endLine]}.
     *       A 500-line legacy function hashes all 500 lines; a 5-line helper hashes 5.</li>
     *   <li>If only a {@code codeSnippet} is available → use the snippet's line count
     *       as the radius (covers at least what the LLM referenced).</li>
     *   <li>Otherwise → radius of 1 (immediate neighbors for drift detection).</li>
     * </ol>
     *
     * @param lineHashes  the file's line hash sequence
     * @param lineNumber  1-based anchor line
     * @param endLine     1-based end line (nullable)
     * @param codeSnippet the LLM's code snippet (nullable)
     * @return context hash, or {@code null} if line is out of range
     */
    static String computeContextHash(
            LineHashSequence lineHashes,
            int lineNumber,
            Integer endLine,
            String codeSnippet
    ) {
        // 1. Exact range when scope boundaries are known
        if (endLine != null && endLine > lineNumber) {
            return lineHashes.getRangeHash(lineNumber, endLine);
        }

        // 2. Snippet-derived radius
        if (codeSnippet != null && !codeSnippet.isBlank()) {
            int snippetLineCount = codeSnippet.split("\\r?\\n").length;
            return lineHashes.getContextHash(lineNumber, Math.max(snippetLineCount, 1));
        }

        // 3. Minimal drift-detection window
        return lineHashes.getContextHash(lineNumber, 1);
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
