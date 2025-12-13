package org.rostilos.codecrow.pipelineagent.generic.processor.analysis;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchFile;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobTriggerSource;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchFileRepository;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.pipelineagent.generic.exception.AnalysisLockedException;
import org.rostilos.codecrow.pipelineagent.generic.service.AnalysisLockService;
import org.rostilos.codecrow.pipelineagent.generic.service.PipelineJobService;
import org.rostilos.codecrow.pipelineagent.generic.service.ProjectService;
import org.rostilos.codecrow.pipelineagent.generic.service.RagIndexTrackingService;
import org.rostilos.codecrow.pipelineagent.generic.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.pipelineagent.generic.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.pipelineagent.generic.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.pipelineagent.generic.client.AiAnalysisClient;
import org.rostilos.codecrow.pipelineagent.rag.service.IncrementalRagUpdateService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import okhttp3.OkHttpClient;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.util.*;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Generic service that handles branch analysis after PR merges or direct commits.
 * Uses VCS-specific services via VcsServiceFactory for provider-specific operations.
 */
@Service
public class BranchAnalysisProcessor {

    private static final Logger log = LoggerFactory.getLogger(BranchAnalysisProcessor.class);

    private final ProjectService projectService;
    private final BranchFileRepository branchFileRepository;
    private final BranchRepository branchRepository;
    private final CodeAnalysisIssueRepository codeAnalysisIssueRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final VcsClientProvider vcsClientProvider;
    private final AiAnalysisClient aiAnalysisClient;
    private final VcsServiceFactory vcsServiceFactory;
    private final AnalysisLockService analysisLockService;
    private final RagIndexTrackingService ragIndexTrackingService;
    private final IncrementalRagUpdateService incrementalRagUpdateService;
    private final PipelineJobService pipelineJobService;

    private static final Pattern DIFF_GIT_PATTERN = Pattern.compile("^diff --git\\s+a/(\\S+)\\s+b/(\\S+)");

    public BranchAnalysisProcessor(
            ProjectService projectService,
            BranchFileRepository branchFileRepository,
            BranchRepository branchRepository,
            CodeAnalysisIssueRepository codeAnalysisIssueRepository,
            BranchIssueRepository branchIssueRepository,
            VcsClientProvider vcsClientProvider,
            AiAnalysisClient aiAnalysisClient,
            VcsServiceFactory vcsServiceFactory,
            AnalysisLockService analysisLockService,
            RagIndexTrackingService ragIndexTrackingService,
            IncrementalRagUpdateService incrementalRagUpdateService,
            PipelineJobService pipelineJobService
    ) {
        this.projectService = projectService;
        this.branchFileRepository = branchFileRepository;
        this.branchRepository = branchRepository;
        this.codeAnalysisIssueRepository = codeAnalysisIssueRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.aiAnalysisClient = aiAnalysisClient;
        this.vcsServiceFactory = vcsServiceFactory;
        this.analysisLockService = analysisLockService;
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.incrementalRagUpdateService = incrementalRagUpdateService;
        this.pipelineJobService = pipelineJobService;
    }

    /**
     * Helper record to hold VCS info from either ProjectVcsConnectionBinding or VcsRepoBinding.
     */
    public record VcsInfo(VcsConnection vcsConnection, String workspace, String repoSlug) {}

    /**
     * Get VCS info from project, preferring ProjectVcsConnectionBinding but falling back to VcsRepoBinding.
     */
    public VcsInfo getVcsInfo(Project project) {
        if (project.getVcsBinding() != null) {
            return new VcsInfo(
                    project.getVcsBinding().getVcsConnection(),
                    project.getVcsBinding().getWorkspace(),
                    project.getVcsBinding().getRepoSlug()
            );
        }
        VcsRepoBinding repoBinding = project.getVcsRepoBinding();
        if (repoBinding != null && repoBinding.getVcsConnection() != null) {
            return new VcsInfo(
                    repoBinding.getVcsConnection(),
                    repoBinding.getExternalNamespace(),
                    repoBinding.getExternalRepoSlug()
            );
        }
        throw new IllegalStateException("No VCS connection configured for project: " + project.getId());
    }

    private EVcsProvider getVcsProvider(Project project) {
        VcsInfo vcsInfo = getVcsInfo(project);
        return vcsInfo.vcsConnection().getProviderType();
    }

    public Map<String, Object> process(BranchProcessRequest request, Consumer<Map<String, Object>> consumer) throws IOException {
        Project project = projectService.getProjectWithConnections(request.getProjectId());

        Optional<String> lockKey = analysisLockService.acquireLockWithWait(
                project,
                request.getTargetBranchName(),
                AnalysisLockType.BRANCH_ANALYSIS,
                request.getCommitHash(),
                null,
                consumer
        );

        if (lockKey.isEmpty()) {
            log.warn("Branch analysis already in progress for project={}, branch={}",
                    project.getId(), request.getTargetBranchName());
            throw new AnalysisLockedException(
                    AnalysisLockType.BRANCH_ANALYSIS.name(),
                    request.getTargetBranchName(),
                    project.getId()
            );
        }

        try {
            consumer.accept(Map.of(
                    "type", "status",
                    "state", "started",
                    "message", "Branch analysis started for branch: " + request.getTargetBranchName()
            ));

            VcsInfo vcsInfo = getVcsInfo(project);

            OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());

            consumer.accept(Map.of(
                    "type", "status",
                    "state", "fetching_diff",
                    "message", "Fetching commit diff"
            ));

            EVcsProvider provider = getVcsProvider(project);
            VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);

            String rawDiff = operationsService.getCommitDiff(
                    client,
                    vcsInfo.workspace(),
                    vcsInfo.repoSlug(),
                    request.getCommitHash()
            );
            log.info("Fetched commit {} diff for branch analysis (no PR context)", request.getCommitHash());

            Set<String> changedFiles = parseFilePathsFromDiff(rawDiff);

            consumer.accept(Map.of(
                    "type", "status",
                    "state", "analyzing_files",
                    "message", "Analyzing " + changedFiles.size() + " changed files"
            ));

            updateBranchFiles(changedFiles, project, request.getTargetBranchName());
            Branch branch = createOrUpdateProjectBranch(project, request);

            mapCodeAnalysisIssuesToBranch(changedFiles, branch, project);
            reanalyzeCandidateIssues(changedFiles, branch, project, request, consumer);

            // Incremental RAG update for merged PR
            performIncrementalRagUpdate(request, project, vcsInfo, rawDiff, consumer);

            log.info("Reconciliation finished (Branch: {}, Commit: {})",
                    request.getTargetBranchName(),
                    request.getCommitHash());

            return Map.of(
                    "status", "accepted",
                    "cached", false,
                    "branch", request.getTargetBranchName()
            );
        } catch (Exception e) {
            log.warn("Branch reconciliation failed (Branch: {}, Commit: {}): {}",
                    request.getTargetBranchName(),
                    request.getCommitHash(),
                    e.getMessage());
            throw e;
        } finally {
            analysisLockService.releaseLock(lockKey.get());
        }
    }

    public Set<String> parseFilePathsFromDiff(String rawDiff) {
        Set<String> files = new HashSet<>();
        if (rawDiff == null || rawDiff.isBlank()) return files;

        String[] lines = rawDiff.split("\\r?\\n");
        for (String line : lines) {
            Matcher m = DIFF_GIT_PATTERN.matcher(line);
            if (m.find()) {
                // prefer the 'b/...' path (second captured group)
                String path = m.group(2);
                if (path != null && !path.isBlank()) {
                    files.add(path);
                }
            }
        }
        return files;
    }

    private void updateBranchFiles(Set<String> changedFiles, Project project, String branchName) {
        VcsInfo vcsInfo = getVcsInfo(project);
        EVcsProvider provider = getVcsProvider(project);
        VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);

        for (String filePath : changedFiles) {
            try {
                OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());

                boolean fileExistsInBranch = operationsService.checkFileExistsInBranch(
                        client,
                        vcsInfo.workspace(),
                        vcsInfo.repoSlug(),
                        branchName,
                        filePath
                );

                if (!fileExistsInBranch) {
                    log.debug("Skipping file {} - does not exist in branch {}", filePath, branchName);
                    continue;
                }
            } catch (Exception e) {
                log.warn("Failed to check file existence for {} in branch {}: {}. Proceeding anyway.",
                    filePath, branchName, e.getMessage());
            }

            List<CodeAnalysisIssue> relatedIssues = codeAnalysisIssueRepository
                    .findByProjectIdAndFilePath(project.getId(), filePath);
            List<CodeAnalysisIssue> branchSpecific = relatedIssues.stream()
                    .filter(issue -> branchName.equals(issue.getAnalysis().getBranchName()) ||
                            branchName.equals(issue.getAnalysis().getSourceBranchName()))
                    .toList();
            long unresolvedCount = branchSpecific.stream().filter(i -> !i.isResolved()).count();

            Optional<BranchFile> projectFileOptional = branchFileRepository
                    .findByProjectIdAndBranchNameAndFilePath(project.getId(), branchName, filePath);
            if (projectFileOptional.isPresent()) {
                BranchFile branchFile = projectFileOptional.get();
                if (branchFile.getIssueCount() != (int) unresolvedCount) {
                    branchFile.setIssueCount((int) unresolvedCount);
                    branchFileRepository.save(branchFile);
                }
            } else {
                BranchFile branchFile = new BranchFile();
                branchFile.setProject(project);
                branchFile.setBranchName(branchName);
                branchFile.setFilePath(filePath);
                branchFile.setIssueCount((int) unresolvedCount);
                branchFileRepository.save(branchFile);
            }
        }
    }

    private Branch createOrUpdateProjectBranch(Project project, BranchProcessRequest request) {
        Branch branch = branchRepository.findByProjectIdAndBranchName(project.getId(), request.getTargetBranchName())
                .orElseGet(() -> {
                    Branch b = new Branch();
                    b.setProject(project);
                    b.setBranchName(request.getTargetBranchName());
                    return b;
                });

        branch.setCommitHash(request.getCommitHash());
        return branchRepository.save(branch);
    }

    private void mapCodeAnalysisIssuesToBranch(Set<String> changedFiles, Branch branch, Project project) {
        VcsInfo vcsInfo = getVcsInfo(project);
        EVcsProvider provider = getVcsProvider(project);
        VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);

        for (String filePath : changedFiles) {
            try {
                OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());

                boolean fileExistsInBranch = operationsService.checkFileExistsInBranch(
                        client,
                        vcsInfo.workspace(),
                        vcsInfo.repoSlug(),
                        branch.getBranchName(),
                        filePath
                );

                if (!fileExistsInBranch) {
                    log.debug("Skipping issue mapping for file {} - does not exist in branch {}",
                        filePath, branch.getBranchName());
                    continue;
                }
            } catch (Exception e) {
                log.warn("Failed to check file existence for {} in branch {}: {}. Proceeding with mapping.",
                    filePath, branch.getBranchName(), e.getMessage());
            }

            List<CodeAnalysisIssue> allIssues = codeAnalysisIssueRepository.findByProjectIdAndFilePath(project.getId(), filePath);

            List<CodeAnalysisIssue> branchSpecificIssues = allIssues.stream()
                    .filter(issue -> {
                        CodeAnalysis analysis = issue.getAnalysis();
                        if (analysis == null) return false;

                        return branch.getBranchName().equals(analysis.getBranchName()) ||
                               branch.getBranchName().equals(analysis.getSourceBranchName());
                    })
                    .toList();

            for (CodeAnalysisIssue issue : branchSpecificIssues) {
                Optional<BranchIssue> existing = branchIssueRepository
                        .findByBranchIdAndCodeAnalysisIssueId(branch.getId(), issue.getId());
                BranchIssue bc;
                if (existing.isPresent()) {
                    bc = existing.get();
                    bc.setSeverity(issue.getSeverity());
                    branchIssueRepository.save(bc);
                } else {
                    bc = new BranchIssue();
                    bc.setBranch(branch);
                    bc.setCodeAnalysisIssue(issue);
                    bc.setResolved(issue.isResolved());
                    bc.setSeverity(issue.getSeverity());
                    bc.setFirstDetectedPrNumber(issue.getAnalysis() != null ? issue.getAnalysis().getPrNumber() : null);
                    branchIssueRepository.save(bc);
                }
            }
        }
    }

    private void reanalyzeCandidateIssues(Set<String> changedFiles, Branch branch, Project project, BranchProcessRequest request, Consumer<Map<String, Object>> consumer) {
        List<BranchIssue> candidateBranchIssues = new ArrayList<>();
        for (String filePath : changedFiles) {
            List<BranchIssue> branchIssues = branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branch.getId(), filePath);
            candidateBranchIssues.addAll(branchIssues);
        }
        if (!candidateBranchIssues.isEmpty()) {
            log.info("Re-analyzing {} pre-existing issues (Branch: {})",
                    candidateBranchIssues.size(),
                    request.getTargetBranchName());

            consumer.accept(Map.of(
                    "type", "status",
                    "state", "reanalyzing_issues",
                    "message", "Re-analyzing " + candidateBranchIssues.size() + " pre-existing issues"
            ));

            try {
                List<CodeAnalysisIssue> candidateIssues = candidateBranchIssues.stream()
                        .map(BranchIssue::getCodeAnalysisIssue)
                        .toList();

                CodeAnalysis tempAnalysis = new CodeAnalysis();
                tempAnalysis.setProject(project);
                tempAnalysis.setAnalysisType(AnalysisType.BRANCH_ANALYSIS);
                tempAnalysis.setPrNumber(null);
                tempAnalysis.setCommitHash(request.getCommitHash());
                tempAnalysis.setBranchName(request.getTargetBranchName());
                tempAnalysis.setIssues(candidateIssues);

                EVcsProvider provider = getVcsProvider(project);
                VcsAiClientService aiClientService = vcsServiceFactory.getAiClientService(provider);

                AiAnalysisRequest aiReq = aiClientService.buildAiAnalysisRequest(
                        project,
                        request,
                        Optional.of(tempAnalysis)
                );

                Map<String, Object> aiResponse = aiAnalysisClient.performAnalysis(aiReq, event -> {
                    try {
                        consumer.accept(event);
                    } catch (Exception ex) {
                        log.warn("Event consumer failed: {}", ex.getMessage());
                    }
                });

                Object issuesObj = aiResponse.get("issues");
                
                if (issuesObj instanceof List) {
                    @SuppressWarnings("unchecked")
                    List<Object> issuesList = (List<Object>) issuesObj;
                    for (Object item : issuesList) {
                        if (item instanceof Map) {
                            @SuppressWarnings("unchecked")
                            Map<String, Object> issueData = (Map<String, Object>) item;
                            processReconciledIssue(issueData, branch, request.getCommitHash());
                        }
                    }
                }
                else if (issuesObj instanceof Map) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> issuesMap = (Map<String, Object>) issuesObj;
                    for (Map.Entry<String, Object> entry : issuesMap.entrySet()) {
                        Object val = entry.getValue();
                        if (val instanceof Map) {
                            @SuppressWarnings("unchecked")
                            Map<String, Object> issueData = (Map<String, Object>) val;
                            processReconciledIssue(issueData, branch, request.getCommitHash());
                        }
                    }
                } else if (issuesObj != null) {
                    log.warn("Issues field is neither List nor Map: {}", issuesObj.getClass().getName());
                }

                Branch refreshedBranch = branchRepository.findByIdWithIssues(branch.getId()).orElse(branch);
                refreshedBranch.updateIssueCounts();
                branchRepository.save(refreshedBranch);
                log.info("Updated branch issue counts after reconciliation: total={}, high={}, medium={}, low={}, resolved={}",
                        refreshedBranch.getTotalIssues(), refreshedBranch.getHighSeverityCount(), 
                        refreshedBranch.getMediumSeverityCount(), refreshedBranch.getLowSeverityCount(), 
                        refreshedBranch.getResolvedCount());
                
                if(project.getDefaultBranch() == null) {
                    project.setDefaultBranch(refreshedBranch);
                }
            } catch (Exception ex) {
                log.warn("Targeted AI re-analysis failed (Branch: {}): {}",
                        request.getTargetBranchName(),
                        ex.getMessage(), ex);
            }
        } else {
            log.info("No pre-existing issues to re-analyze (Branch: {})",
                    request.getTargetBranchName());
        }
    }

    private void processReconciledIssue(Map<String, Object> issueData, Branch branch, String commitHash) {
        Object issueIdFromAi = issueData.get("issueId");
        Long actualIssueId = null;

        if (issueIdFromAi != null) {
            try {
                actualIssueId = Long.parseLong(String.valueOf(issueIdFromAi));
            } catch (NumberFormatException e) {
                log.warn("Invalid issueId in AI response: {}", issueIdFromAi);
            }
        }

        Object isResolvedObj = issueData.get("isResolved");
        boolean resolved = false;
        if (isResolvedObj instanceof Boolean) {
            resolved = (Boolean) isResolvedObj;
        } else if (issueData.get("status") != null &&
                "resolved".equalsIgnoreCase(String.valueOf(issueData.get("status")))) {
            resolved = true;
        }

        if (resolved && actualIssueId != null) {
            Optional<BranchIssue> branchIssueOpt = branchIssueRepository
                    .findByBranchIdAndCodeAnalysisIssueId(branch.getId(), actualIssueId);
            if (branchIssueOpt.isPresent()) {
                BranchIssue bi = branchIssueOpt.get();
                if (!bi.isResolved()) {
                    bi.setResolved(true);
                    bi.setResolvedInPrNumber(null);
                    bi.setResolvedInCommitHash(commitHash);
                    branchIssueRepository.save(bi);

                    Optional<CodeAnalysisIssue> caiOpt = codeAnalysisIssueRepository.findById(actualIssueId);
                    if (caiOpt.isPresent()) {
                        CodeAnalysisIssue cai = caiOpt.get();
                        cai.setResolved(true);
                        codeAnalysisIssueRepository.save(cai);
                    }
                    log.info("Marked branch issue {} as resolved (commit: {})",
                            actualIssueId,
                            commitHash);
                }
            }
        }
    }

    private void performIncrementalRagUpdate(
            BranchProcessRequest request,
            Project project,
            VcsInfo vcsInfo,
            String rawDiff,
            Consumer<Map<String, Object>> consumer
    ) {
        Job job = null;
        try {
            if (!incrementalRagUpdateService.shouldPerformIncrementalUpdate(project)) {
                log.debug("Skipping RAG incremental update - not enabled or not yet indexed");
                return;
            }

            String branch = request.getTargetBranchName();
            String commit = request.getCommitHash();

            job = pipelineJobService.createPipelineRagJob(project, false, JobTriggerSource.WEBHOOK);
            pipelineJobService.getJobService().info(job, "rag_init", 
                    String.format("Starting incremental RAG update for branch '%s' (commit: %s)", branch, commit));

            Optional<String> ragLockKey = analysisLockService.acquireLock(
                    project,
                    branch,
                    AnalysisLockType.RAG_INDEXING,
                    commit,
                    null
            );

            if (ragLockKey.isEmpty()) {
                log.warn("RAG update already in progress for project={}, branch={}",
                        project.getId(), branch);
                pipelineJobService.getJobService().warn(job, "rag_skip", "RAG update already in progress - skipping");
                pipelineJobService.failJob(job, "RAG update already in progress");
                return;
            }

            try {
                consumer.accept(Map.of(
                        "type", "status",
                        "state", "rag_update",
                        "message", "Updating RAG index with changed files"
                ));

                ragIndexTrackingService.markUpdatingStarted(project, branch, commit);

                IncrementalRagUpdateService.DiffResult diffResult = 
                        incrementalRagUpdateService.parseDiffForRag(rawDiff);

                Set<String> addedOrModified = diffResult.addedOrModified();
                Set<String> deleted = diffResult.deleted();

                if (addedOrModified.isEmpty() && deleted.isEmpty()) {
                    Set<String> changedFiles = parseFilePathsFromDiff(rawDiff);
                    addedOrModified = changedFiles;
                }

                if (addedOrModified.isEmpty() && deleted.isEmpty()) {
                    log.info("No files to update in RAG index");
                    ragIndexTrackingService.markUpdatingCompleted(project, branch, commit, 0);
                    pipelineJobService.getJobService().info(job, "rag_complete", "No files to update in RAG index");
                    pipelineJobService.completeJob(job, null);
                    return;
                }

                log.info("Performing incremental RAG update: {} files to update, {} to delete",
                        addedOrModified.size(), deleted.size());
                pipelineJobService.getJobService().info(job, "rag_update", 
                        String.format("Performing incremental RAG update: %d files to update, %d to delete", 
                                addedOrModified.size(), deleted.size()));

                Map<String, Object> result = incrementalRagUpdateService.performIncrementalUpdate(
                        project,
                        vcsInfo.vcsConnection(),
                        vcsInfo.workspace(),
                        vcsInfo.repoSlug(),
                        branch,
                        commit,
                        addedOrModified,
                        deleted
                );

                Integer filesUpdated = result.get("updatedFiles") != null 
                        ? ((Number) result.get("updatedFiles")).intValue() 
                        : 0;
                ragIndexTrackingService.markUpdatingCompleted(project, branch, commit, filesUpdated);

                consumer.accept(Map.of(
                        "type", "status",
                        "state", "rag_complete",
                        "message", "RAG index updated with " + filesUpdated + " files"
                ));

                log.info("Incremental RAG update completed: {}", result);
                pipelineJobService.getJobService().info(job, "rag_complete", 
                        String.format("Incremental RAG update completed: %d files updated", filesUpdated));
                pipelineJobService.completeJob(job, null);

            } catch (Exception e) {
                ragIndexTrackingService.markIndexingFailed(project, e.getMessage());
                log.error("RAG incremental update failed", e);
                if (job != null) {
                    pipelineJobService.getJobService().error(job, "rag_error", "RAG incremental update failed: " + e.getMessage());
                    pipelineJobService.failJob(job, "RAG incremental update failed: " + e.getMessage());
                }
            } finally {
                analysisLockService.releaseLock(ragLockKey.get());
            }
        } catch (Exception e) {
            log.warn("RAG incremental update failed (non-critical): {}", e.getMessage());
            if (job != null) {
                pipelineJobService.getJobService().error(job, "rag_error", "RAG incremental update failed (non-critical): " + e.getMessage());
                pipelineJobService.failJob(job, e.getMessage());
            }
        }
    }
}
