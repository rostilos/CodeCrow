package org.rostilos.codecrow.analysisengine.processor.analysis.branch;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.service.FileSnapshotService;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;
import org.rostilos.codecrow.core.util.tracking.TrackingConfidence;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.function.Consumer;

/**
 * Handles issue lifecycle operations on branch issues:
 * <ul>
 *   <li>Diff-based line-number reconciliation</li>
 *   <li>Snippet-based line-number verification</li>
 *   <li>Deterministic tracking (content-based) + AI fallback</li>
 *   <li>Issue resolution</li>
 * </ul>
 */
@Service
public class BranchIssueReconciliationService {

    private static final Logger log = LoggerFactory.getLogger(BranchIssueReconciliationService.class);

    private final BranchIssueRepository branchIssueRepository;
    private final BranchRepository branchRepository;
    private final FileSnapshotService fileSnapshotService;
    private final VcsServiceFactory vcsServiceFactory;
    private final VcsClientProvider vcsClientProvider;
    private final AiAnalysisClient aiAnalysisClient;

    public BranchIssueReconciliationService(
            BranchIssueRepository branchIssueRepository,
            BranchRepository branchRepository,
            FileSnapshotService fileSnapshotService,
            VcsServiceFactory vcsServiceFactory,
            VcsClientProvider vcsClientProvider,
            AiAnalysisClient aiAnalysisClient) {
        this.branchIssueRepository = branchIssueRepository;
        this.branchRepository = branchRepository;
        this.fileSnapshotService = fileSnapshotService;
        this.vcsServiceFactory = vcsServiceFactory;
        this.vcsClientProvider = vcsClientProvider;
        this.aiAnalysisClient = aiAnalysisClient;
    }

    // ═══════════════════════ Diff-based line reconciliation ══════════════════

    /**
     * For every changed file that still exists in the branch, update the
     * {@code currentLineNumber} of each unresolved {@link BranchIssue} so
     * that it reflects the line's position <em>after</em> the diff was applied.
     * <p>
     * CodeAnalysisIssue records remain immutable historical records.
     */
    public void reconcileIssueLineNumbers(String rawDiff, Set<String> changedFiles,
                                           Branch branch) {
        if (rawDiff == null || rawDiff.isBlank()) return;

        Map<String, String> perFileDiffs = DiffParsingUtils.splitDiffByFile(rawDiff);

        for (String filePath : changedFiles) {
            String fileDiff = perFileDiffs.get(filePath);
            if (fileDiff == null) continue;

            List<BranchIssue> issues = branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branch.getId(), filePath);
            if (issues.isEmpty()) continue;

            List<int[]> hunks = DiffParsingUtils.parseHunks(fileDiff);
            if (hunks.isEmpty()) continue;

            int updated = 0;
            for (BranchIssue bi : issues) {
                Integer lineNum = bi.getCurrentLineNumber() != null
                        ? bi.getCurrentLineNumber() : bi.getLineNumber();
                if (lineNum == null || lineNum <= 0) continue;

                int newLine = DiffParsingUtils.mapLineNumber(lineNum, hunks, fileDiff);
                if (newLine != lineNum) {
                    bi.setCurrentLineNumber(newLine);
                    branchIssueRepository.save(bi);
                    updated++;
                }
            }

            if (updated > 0) {
                log.info("Reconciled line numbers for {} branch issues in {} (branch: {})",
                        updated, filePath, branch.getBranchName());
            }
        }
    }

    // ═══════════════════════ Snippet-based verification ══════════════════════

    /**
     * Re-verify all branch issue line numbers for changed files using their
     * persisted {@code codeSnippet}. This catches any drift that diff-based
     * remapping missed.
     * <p>
     * CodeAnalysisIssue records remain immutable historical records.
     */
    public void verifyIssueLineNumbersWithSnippets(Set<String> changedFiles,
                                                    Project project, Branch branch) {
        for (String filePath : changedFiles) {
            Optional<String> contentOpt = fileSnapshotService.getFileContentForBranch(
                    project.getId(), branch.getBranchName(), filePath);
            if (contentOpt.isEmpty()) continue;

            LineHashSequence lineHashes = LineHashSequence.from(contentOpt.get());
            if (lineHashes.getLineCount() == 0) continue;

            List<BranchIssue> issues = branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branch.getId(), filePath)
                    .stream()
                    .filter(bi -> bi.getCodeSnippet() != null && !bi.getCodeSnippet().isBlank())
                    .toList();
            if (issues.isEmpty()) continue;

            int corrected = 0;
            for (BranchIssue bi : issues) {
                Integer currentLine = bi.getCurrentLineNumber() != null
                        ? bi.getCurrentLineNumber() : bi.getLineNumber();
                if (currentLine == null || currentLine <= 0) continue;

                String snippetHash = LineHashSequence.hashLine(bi.getCodeSnippet());

                // Check if current line already matches
                if (currentLine <= lineHashes.getLineCount()) {
                    String currentHash = lineHashes.getHashForLine(currentLine);
                    if (snippetHash.equals(currentHash)) continue;
                }

                // Line drifted — find the real position
                int foundLine = lineHashes.findClosestLineForHash(snippetHash, currentLine);
                if (foundLine > 0 && foundLine != currentLine) {
                    log.info("Snippet verification corrected branch issue {} in {}:{} -> {} (snippet: \"{}\")",
                            bi.getId(), filePath, currentLine, foundLine,
                            bi.getCodeSnippet().length() > 60
                                    ? bi.getCodeSnippet().substring(0, 57) + "..."
                                    : bi.getCodeSnippet());
                    bi.setCurrentLineNumber(foundLine);
                    bi.setCurrentLineHash(lineHashes.getHashForLine(foundLine));
                    bi.setLineHashContext(lineHashes.getContextHash(foundLine, 2));
                    branchIssueRepository.save(bi);
                    corrected++;
                }
            }

            if (corrected > 0) {
                log.info("Snippet verification corrected {} branch issue line numbers in {} (branch: {})",
                        corrected, filePath, branch.getBranchName());
            }
        }
    }

    // ═══════════════════════ Full re-analysis pipeline ═══════════════════════

    /**
     * Re-analyzes candidate issues for changed files using a three-stage pipeline:
     * <ol>
     *   <li><b>Rule-based pre-filter</b> — auto-resolve issues for deleted files</li>
     *   <li><b>Deterministic tracking</b> — content-based identity verification</li>
     *   <li><b>AI reconciliation</b> — fallback for ambiguous issues</li>
     * </ol>
     */
    public void reanalyzeCandidateIssues(Set<String> changedFiles,
                                          Set<String> filesExistingInBranch,
                                          Branch branch, Project project,
                                          BranchProcessRequest request,
                                          Consumer<Map<String, Object>> consumer,
                                          Map<String, String> archiveContents) {
        List<BranchIssue> candidateBranchIssues = collectCandidateIssues(changedFiles, branch);
        if (candidateBranchIssues.isEmpty()) {
            log.info("No pre-existing issues to re-analyze (Branch: {})", request.getTargetBranchName());
            return;
        }

        // Stage 1: auto-resolve deleted-file issues
        PartitionResult partitioned = partitionByFileExistence(
                candidateBranchIssues, filesExistingInBranch);

        if (!partitioned.autoResolved.isEmpty()) {
            log.info("Auto-resolving {} issues for deleted files (Branch: {})",
                    partitioned.autoResolved.size(), request.getTargetBranchName());
            for (BranchIssue issue : partitioned.autoResolved) {
                resolveIssue(issue, request.getCommitHash(), request.getSourcePrNumber(),
                        "File deleted from branch", "file-deletion");
            }
        }

        if (partitioned.needsTracking.isEmpty()) {
            log.info("All {} pre-existing issues auto-resolved via file-existence check (Branch: {})",
                    partitioned.autoResolved.size(), request.getTargetBranchName());
            updateBranchCountsAfterReconciliation(branch);
            return;
        }

        // Stage 2: deterministic content-based tracking
        consumer.accept(Map.of(
                "type", "status",
                "state", "tracking_issues",
                "message", "Tracking " + partitioned.needsTracking.size()
                        + " pre-existing issues deterministically ("
                        + partitioned.autoResolved.size() + " auto-resolved)"));

        TrackingResult tracking = performDeterministicTracking(
                partitioned.needsTracking, project, request, archiveContents);

        // Resolve deterministically-resolved issues
        if (!tracking.resolved.isEmpty()) {
            log.info("Deterministically resolving {} issues (no longer match current code) (Branch: {})",
                    tracking.resolved.size(), request.getTargetBranchName());
            for (BranchIssue bi : tracking.resolved) {
                resolveIssue(bi, request.getCommitHash(), request.getSourcePrNumber(),
                        "Issue no longer matches current code (deterministic tracking)",
                        "deterministic-tracking");
            }
        }

        log.info("Deterministic tracking results: {} confirmed, {} resolved, {} need AI (Branch: {})",
                tracking.confirmed.size(), tracking.resolved.size(),
                tracking.needsAi.size(), request.getTargetBranchName());

        // Stage 3: AI reconciliation for ambiguous issues
        if (!tracking.needsAi.isEmpty()) {
            performAiReconciliation(tracking.needsAi, tracking.fetchedFileContents,
                    branch, project, request, consumer, archiveContents);
        }

        updateBranchCountsAfterReconciliation(branch);
    }

    // ═══════════════════════ Issue resolution ════════════════════════════════

    /**
     * Resolve a BranchIssue. The underlying CodeAnalysisIssue is NOT mutated.
     */
    public void resolveIssue(BranchIssue bi, String commitHash, Long prNumber,
                              String description, String resolvedBy) {
        java.time.OffsetDateTime now = java.time.OffsetDateTime.now();
        bi.setResolved(true);
        bi.setResolvedAt(now);
        bi.setResolvedInCommitHash(commitHash);
        bi.setResolvedDescription(description);
        bi.setResolvedBy(resolvedBy);
        if (prNumber != null) {
            bi.setResolvedInPrNumber(prNumber);
        }
        branchIssueRepository.save(bi);
    }

    /**
     * Refresh branch from DB and update issue counts after any reconciliation.
     */
    public void updateBranchCountsAfterReconciliation(Branch branch) {
        Branch refreshedBranch = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
        refreshedBranch.updateIssueCounts();
        branchRepository.save(refreshedBranch);
        log.info("Updated branch issue counts after reconciliation: total={}, high={}, medium={}, low={}, resolved={}",
                refreshedBranch.getTotalIssues(), refreshedBranch.getHighSeverityCount(),
                refreshedBranch.getMediumSeverityCount(), refreshedBranch.getLowSeverityCount(),
                refreshedBranch.getResolvedCount());
    }

    /**
     * Process a single reconciled issue from the AI response.
     * Returns {@code true} if the issue was actually resolved.
     */
    public boolean processReconciledIssue(Map<String, Object> issueData, Branch branch,
                                           String commitHash, Long sourcePrNumber) {
        Long actualIssueId = extractIssueId(issueData);
        if (actualIssueId == null) return false;

        boolean resolved = isMarkedResolved(issueData);
        String resolvedDescription = issueData.get("reason") != null
                ? String.valueOf(issueData.get("reason")) : null;

        if (!resolved) return false;

        Optional<BranchIssue> branchIssueOpt = branchIssueRepository
                .findByBranchIdAndOriginIssueId(branch.getId(), actualIssueId);
        if (branchIssueOpt.isEmpty()) {
            branchIssueOpt = branchIssueRepository.findById(actualIssueId)
                    .filter(bi -> bi.getBranch().getId().equals(branch.getId()));
        }

        if (branchIssueOpt.isPresent()) {
            BranchIssue bi = branchIssueOpt.get();
            if (!bi.isResolved()) {
                java.time.OffsetDateTime now = java.time.OffsetDateTime.now();
                bi.setResolved(true);
                bi.setResolvedInPrNumber(sourcePrNumber);
                bi.setResolvedInCommitHash(commitHash);
                bi.setResolvedDescription(resolvedDescription);
                bi.setResolvedAt(now);
                bi.setResolvedBy("AI-reconciliation");
                branchIssueRepository.save(bi);

                log.info("Marked branch issue {} (origin CAI: {}) as resolved (commit: {}, PR: {}, description: {})",
                        bi.getId(), actualIssueId, commitHash, sourcePrNumber,
                        resolvedDescription != null
                                ? resolvedDescription.substring(0, Math.min(100, resolvedDescription.length()))
                                : "none");
                return true;
            }
            return false;
        }
        log.debug("No BranchIssue found for origin issue {} in branch {} — may have been created before migration",
                actualIssueId, branch.getId());
        return false;
    }

    // ═══════════════════════ Private helpers ═════════════════════════════════

    private List<BranchIssue> collectCandidateIssues(Set<String> changedFiles, Branch branch) {
        List<BranchIssue> candidates = new ArrayList<>();
        for (String filePath : changedFiles) {
            candidates.addAll(branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branch.getId(), filePath));
        }
        return candidates;
    }

    /** Partition: record holding auto-resolved vs needs-tracking lists. */
    private record PartitionResult(List<BranchIssue> autoResolved,
                                   List<BranchIssue> needsTracking) {}

    private PartitionResult partitionByFileExistence(List<BranchIssue> candidates,
                                                     Set<String> filesExistingInBranch) {
        List<BranchIssue> autoResolved = new ArrayList<>();
        List<BranchIssue> needsTracking = new ArrayList<>();
        for (BranchIssue issue : candidates) {
            String fp = issue.getFilePath();
            if (fp != null && !filesExistingInBranch.contains(fp)) {
                autoResolved.add(issue);
            } else {
                needsTracking.add(issue);
            }
        }
        return new PartitionResult(autoResolved, needsTracking);
    }

    /** Result of deterministic tracking pass. */
    private record TrackingResult(List<BranchIssue> confirmed,
                                  List<BranchIssue> resolved,
                                  List<BranchIssue> needsAi,
                                  Map<String, String> fetchedFileContents) {}

    private TrackingResult performDeterministicTracking(
            List<BranchIssue> needsTracking, Project project,
            BranchProcessRequest request, Map<String, String> archiveContents) {

        // Resolve VCS clients for fallback file content fetching
        var vcsRepoInfo = project.getEffectiveVcsRepoInfo();
        EVcsProvider provider = vcsRepoInfo.getVcsConnection().getProviderType();
        VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);
        OkHttpClient client = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());

        // Group issues by file
        Map<String, List<BranchIssue>> issuesByFile = new LinkedHashMap<>();
        for (BranchIssue bi : needsTracking) {
            String fp = bi.getFilePath();
            if (fp != null) {
                issuesByFile.computeIfAbsent(fp, k -> new ArrayList<>()).add(bi);
            }
        }

        List<BranchIssue> confirmed = new ArrayList<>();
        List<BranchIssue> resolved = new ArrayList<>();
        List<BranchIssue> needsAi = new ArrayList<>();

        // Pre-populate file contents from archive
        Map<String, String> fetchedFileContents = new LinkedHashMap<>();
        if (archiveContents != null && !archiveContents.isEmpty()) {
            for (String fp : issuesByFile.keySet()) {
                String content = archiveContents.get(fp);
                if (content != null) {
                    fetchedFileContents.put(fp, content);
                }
            }
            log.info("Pre-populated {} file contents from archive for deterministic tracking",
                    fetchedFileContents.size());
        }

        for (Map.Entry<String, List<BranchIssue>> entry : issuesByFile.entrySet()) {
            String filePath = entry.getKey();
            List<BranchIssue> fileIssues = entry.getValue();

            LineHashSequence currentHashes;
            try {
                String fileContent = fetchedFileContents.get(filePath);
                if (fileContent == null) {
                    fileContent = operationsService.getFileContent(
                            client, vcsRepoInfo.getRepoWorkspace(), vcsRepoInfo.getRepoSlug(),
                            request.getCommitHash(), filePath);
                }
                currentHashes = fileContent != null
                        ? LineHashSequence.from(fileContent) : LineHashSequence.empty();
                if (fileContent != null) {
                    fetchedFileContents.put(filePath, fileContent);
                }
            } catch (Exception e) {
                log.warn("Failed to fetch file content for {}: {}. Falling back to AI reconciliation.",
                        filePath, e.getMessage());
                needsAi.addAll(fileIssues);
                continue;
            }

            classifyIssuesByContent(fileIssues, currentHashes, request, confirmed, resolved, needsAi);
        }

        return new TrackingResult(confirmed, resolved, needsAi, fetchedFileContents);
    }

    /**
     * Classify each issue in a file as confirmed, resolved, or needing AI,
     * based on content anchors (codeSnippet, lineHash).
     */
    private void classifyIssuesByContent(List<BranchIssue> fileIssues,
                                          LineHashSequence currentHashes,
                                          BranchProcessRequest request,
                                          List<BranchIssue> confirmed,
                                          List<BranchIssue> resolved,
                                          List<BranchIssue> needsAi) {
        for (BranchIssue bi : fileIssues) {
            Integer currentLine = bi.getCurrentLineNumber() != null
                    ? bi.getCurrentLineNumber() : bi.getLineNumber();
            boolean contentFound = false;
            String updatedLineHash = null;

            // Issues at line <= 1 with no codeSnippet have no reliable anchor
            boolean hasNoReliableAnchor = (currentLine == null || currentLine <= 1)
                    && (bi.getCodeSnippet() == null || bi.getCodeSnippet().isBlank());
            if (hasNoReliableAnchor) {
                needsAi.add(bi);
                continue;
            }

            // 1st priority: codeSnippet
            if (bi.getCodeSnippet() != null && !bi.getCodeSnippet().isBlank()
                    && currentHashes.getLineCount() > 0) {
                String snippetHash = LineHashSequence.hashLine(bi.getCodeSnippet());
                int foundLine = currentHashes.findClosestLineForHash(
                        snippetHash, currentLine != null ? currentLine : 1);
                if (foundLine > 0) {
                    currentLine = foundLine;
                    updatedLineHash = currentHashes.getHashForLine(foundLine);
                    contentFound = true;
                }
            }

            // 2nd priority: lineHash
            if (!contentFound && bi.getLineHash() != null
                    && currentHashes.getLineCount() > 0) {
                int foundLine = currentHashes.findClosestLineForHash(
                        bi.getLineHash(), currentLine != null ? currentLine : 1);
                if (foundLine > 0) {
                    currentLine = foundLine;
                    updatedLineHash = bi.getLineHash();
                    contentFound = true;
                }
            }

            if (contentFound) {
                bi.setCurrentLineNumber(currentLine);
                bi.setCurrentLineHash(updatedLineHash);
                bi.setLastVerifiedCommit(request.getCommitHash());
                bi.setTrackingConfidence(TrackingConfidence.EXACT);
                branchIssueRepository.save(bi);
                confirmed.add(bi);
            } else if (bi.getCodeSnippet() != null && !bi.getCodeSnippet().isBlank()) {
                resolved.add(bi);
            } else if (bi.getLineHash() != null) {
                resolved.add(bi);
            } else {
                needsAi.add(bi);
            }
        }
    }

    /**
     * AI reconciliation fallback for issues that couldn't be resolved
     * deterministically.
     */
    private void performAiReconciliation(List<BranchIssue> needsAiReconciliation,
                                          Map<String, String> fetchedFileContents,
                                          Branch branch, Project project,
                                          BranchProcessRequest request,
                                          Consumer<Map<String, Object>> consumer,
                                          Map<String, String> archiveContents) {
        consumer.accept(Map.of(
                "type", "status",
                "state", "reanalyzing_issues",
                "message", "AI reconciliation for " + needsAiReconciliation.size() + " ambiguous issues"));

        try {
            List<AiRequestPreviousIssueDTO> previousIssueDTOs = needsAiReconciliation.stream()
                    .map(AiRequestPreviousIssueDTO::fromBranchIssue)
                    .toList();

            var vcsRepoInfo = project.getEffectiveVcsRepoInfo();
            EVcsProvider provider = vcsRepoInfo.getVcsConnection().getProviderType();
            VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);
            VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());

            // Build file contents for AI — only files that have issues needing reconciliation
            Map<String, String> aiFileContents = buildAiFileContents(
                    needsAiReconciliation, fetchedFileContents, archiveContents,
                    operationsService, client, vcsRepoInfo.getRepoWorkspace(),
                    vcsRepoInfo.getRepoSlug(), request.getCommitHash());

            log.info("Sending {} file contents for MCP-free AI reconciliation", aiFileContents.size());

            AiAnalysisRequest aiReq = aiClientService.buildAiAnalysisRequestForBranchReconciliation(
                    project, request, previousIssueDTOs, aiFileContents);

            Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiReq, event -> {
                try { consumer.accept(event); } catch (Exception ex) {
                    log.warn("Event consumer failed: {}", ex.getMessage());
                }
            });

            int aiResolvedCount = processAiResponse(aiResponse, branch,
                    request.getCommitHash(), request.getSourcePrNumber());

            log.info("AI reconciliation result: {} issues resolved out of {} sent to AI (Branch: {})",
                    aiResolvedCount, needsAiReconciliation.size(), request.getTargetBranchName());

            if (project.getDefaultBranch() == null) {
                project.setDefaultBranch(branch);
            }
        } catch (Exception ex) {
            log.warn("AI reconciliation failed (Branch: {}): {}",
                    request.getTargetBranchName(), ex.getMessage(), ex);
        }
    }

    private Map<String, String> buildAiFileContents(
            List<BranchIssue> issues, Map<String, String> fetchedFileContents,
            Map<String, String> archiveContents,
            VcsOperationsService operationsService, OkHttpClient client,
            String workspace, String repoSlug, String commitHash) {

        Map<String, String> aiFileContents = new LinkedHashMap<>();

        // Add already-fetched content for files with issues
        for (BranchIssue bi : issues) {
            String fp = bi.getFilePath();
            if (fp != null && fetchedFileContents.containsKey(fp)) {
                aiFileContents.put(fp, fetchedFileContents.get(fp));
            }
        }

        // Fetch any missing files — archive first, then per-file API
        for (BranchIssue bi : issues) {
            String fp = bi.getFilePath();
            if (fp != null && !aiFileContents.containsKey(fp)) {
                if (archiveContents != null && archiveContents.containsKey(fp)) {
                    aiFileContents.put(fp, archiveContents.get(fp));
                } else {
                    try {
                        String content = operationsService.getFileContent(
                                client, workspace, repoSlug, commitHash, fp);
                        if (content != null) {
                            aiFileContents.put(fp, content);
                        }
                    } catch (Exception e) {
                        log.warn("Failed to fetch file content for AI reconciliation: {}", fp);
                    }
                }
            }
        }
        return aiFileContents;
    }

    @SuppressWarnings("unchecked")
    private int processAiResponse(Map<String, Object> aiResponse, Branch branch,
                                  String commitHash, Long sourcePrNumber) {
        Object issuesObj = aiResponse.get("issues");
        int aiResolvedCount = 0;

        if (issuesObj instanceof List) {
            List<Object> issuesList = (List<Object>) issuesObj;
            for (Object item : issuesList) {
                if (item instanceof Map) {
                    if (processReconciledIssue((Map<String, Object>) item, branch, commitHash, sourcePrNumber)) {
                        aiResolvedCount++;
                    }
                }
            }
        } else if (issuesObj instanceof Map) {
            Map<String, Object> issuesMap = (Map<String, Object>) issuesObj;
            for (Map.Entry<String, Object> mapEntry : issuesMap.entrySet()) {
                Object val = mapEntry.getValue();
                if (val instanceof Map) {
                    if (processReconciledIssue((Map<String, Object>) val, branch, commitHash, sourcePrNumber)) {
                        aiResolvedCount++;
                    }
                }
            }
        } else if (issuesObj != null) {
            log.warn("Issues field is neither List nor Map: {}", issuesObj.getClass().getName());
        }
        return aiResolvedCount;
    }

    private Long extractIssueId(Map<String, Object> issueData) {
        Object issueIdFromAi = issueData.get("issueId");
        if (issueIdFromAi == null) {
            issueIdFromAi = issueData.get("id");
        }
        if (issueIdFromAi != null) {
            try {
                return Long.parseLong(String.valueOf(issueIdFromAi));
            } catch (NumberFormatException e) {
                log.warn("Invalid issueId in AI response: {}", issueIdFromAi);
            }
        }
        return null;
    }

    private boolean isMarkedResolved(Map<String, Object> issueData) {
        Object isResolvedObj = issueData.get("isResolved");
        if (isResolvedObj instanceof Boolean) {
            return (Boolean) isResolvedObj;
        }
        return issueData.get("status") != null
                && "resolved".equalsIgnoreCase(String.valueOf(issueData.get("status")));
    }
}
