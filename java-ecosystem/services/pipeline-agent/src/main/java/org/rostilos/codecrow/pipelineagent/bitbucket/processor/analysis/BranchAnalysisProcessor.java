package org.rostilos.codecrow.pipelineagent.bitbucket.processor.analysis;

import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.branch.BranchIssue;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchFile;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchFileRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.pipelineagent.bitbucket.service.BitbucketAiClientService;
import org.rostilos.codecrow.pipelineagent.bitbucket.service.BitbucketReportingService;
import org.rostilos.codecrow.pipelineagent.generic.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.pipelineagent.generic.exception.AnalysisLockedException;
import org.rostilos.codecrow.pipelineagent.generic.service.AnalysisLockService;
import org.rostilos.codecrow.pipelineagent.generic.service.ProjectService;
import org.rostilos.codecrow.pipelineagent.generic.service.RagIndexTrackingService;
import org.rostilos.codecrow.pipelineagent.generic.client.AiAnalysisClient;
import org.rostilos.codecrow.pipelineagent.rag.service.RagIndexingService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.CheckFileExistsInBranchAction;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.GetCommitDiffAction;
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
 * Service that reconciles code-analysis issues against a branch after a PR merge.
 * Responsibilities:
 *  - fetch PR diff from VCS,
 *  - extract changed file paths,
 *  - maintain project_file table (create / update issue_count),
 *  - maintain project_branch (create / update commit hash),
 *  - create branch_issue mappings for issues touching changed files.
 */
@Service
public class BranchAnalysisProcessor extends AbstractAnalysisProcessor {

    private static final Logger log = LoggerFactory.getLogger(BranchAnalysisProcessor.class);

    private final ProjectService projectService;
    private final BranchFileRepository branchFileRepository;
    private final BranchRepository branchRepository;
    private final CodeAnalysisIssueRepository codeAnalysisIssueRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final VcsClientProvider vcsClientProvider;
    private final AiAnalysisClient aiAnalysisClient;
    private final BitbucketAiClientService bitbucketAiClientService;
    private final RagIndexingService ragIndexingService;
    private final AnalysisLockService analysisLockService;
    private final RagIndexTrackingService ragIndexTrackingService;

    private static final Pattern DIFF_GIT_PATTERN = Pattern.compile("^diff --git\\s+a/(\\S+)\\s+b/(\\S+)");

    public BranchAnalysisProcessor(
            ProjectService projectService,
            BranchFileRepository branchFileRepository,
            BranchRepository branchRepository,
            CodeAnalysisIssueRepository codeAnalysisIssueRepository,
            BranchIssueRepository branchIssueRepository,
            VcsClientProvider vcsClientProvider,
            AiAnalysisClient aiAnalysisClient,
            BitbucketAiClientService bitbucketAiClientService,
            CodeAnalysisService codeAnalysisService,
            BitbucketReportingService reportingService,
            RagIndexingService ragIndexingService,
            AnalysisLockService analysisLockService,
            RagIndexTrackingService ragIndexTrackingService
    ) {
        super(codeAnalysisService, reportingService);
        this.projectService = projectService;
        this.branchFileRepository = branchFileRepository;
        this.branchRepository = branchRepository;
        this.codeAnalysisIssueRepository = codeAnalysisIssueRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.aiAnalysisClient = aiAnalysisClient;
        this.bitbucketAiClientService = bitbucketAiClientService;
        this.ragIndexingService = ragIndexingService;
        this.analysisLockService = analysisLockService;
        this.ragIndexTrackingService = ragIndexTrackingService;
    }

    /**
     * Helper record to hold VCS info from either ProjectVcsConnectionBinding or VcsRepoBinding.
     */
    private record VcsInfo(VcsConnection vcsConnection, String workspace, String repoSlug) {}

    /**
     * Get VCS info from project, preferring ProjectVcsConnectionBinding but falling back to VcsRepoBinding.
     */
    private VcsInfo getVcsInfo(Project project) {
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

    /**
     * Handle reconciliation after a PR merge. Expects the same BitbucketWebhookRequest payload that is used for analysis.
     *
     * Steps:
     *  - load project and VCS client
     *  - fetch PR diff
     *  - parse changed file paths
     *  - update/create ProjectFile rows with issue_count
     *  - update/create ProjectBranch row
     *  - for each related CodeAnalysisIssue create/update BranchCodeAnalysisIssue mapping
     *
     * This method intentionally keeps the AI re-analysis out of scope for the first pass; it prepares database records
     * for a later, targeted re-analysis.
     */
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

            GetCommitDiffAction commitDiffAction = new GetCommitDiffAction(client);
            String rawDiff = commitDiffAction.getCommitDiff(
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

            //TODO: MVP2, rag pipelines
            //indexRepositoryInRAG(request, project, changedFiles);

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

    private Set<String> parseFilePathsFromDiff(String rawDiff) {
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
        for (String filePath : changedFiles) {
            // Check if file actually exists in the target branch
            // This filters out files from unmerged PRs that only exist in feature branches
            try {
                OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());

                CheckFileExistsInBranchAction checkFileAction = new CheckFileExistsInBranchAction(client);
                boolean fileExistsInBranch = checkFileAction.fileExists(
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
                // Continue processing if check fails - better to include potentially non-existent file
                // than to skip a valid one
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
        for (String filePath : changedFiles) {
            // Verify file exists in branch before mapping issues
            try {
                OkHttpClient client = vcsClientProvider.getHttpClient(vcsInfo.vcsConnection());

                CheckFileExistsInBranchAction checkFileAction = new CheckFileExistsInBranchAction(client);
                boolean fileExistsInBranch = checkFileAction.fileExists(
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

            // Filter issues to only include those from the current branch
            // This prevents mapping issues from other branches (e.g., feature branches) to this branch
            List<CodeAnalysisIssue> branchSpecificIssues = allIssues.stream()
                    .filter(issue -> {
                        CodeAnalysis analysis = issue.getAnalysis();
                        if (analysis == null) return false;

                        // Include if this issue was found in analysis targeting this branch
                        // or from a PR that was merged into this branch (sourceBranch -> targetBranch)
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

    // Prepare candidate issues for targeted AI re-analysis:
    // - Only unresolved issues
    // - Only issues in changed files
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

                AiAnalysisRequest aiReq = bitbucketAiClientService.buildAiAnalysisRequest(
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
                if (issuesObj instanceof Map) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> issuesMap = (Map<String, Object>) issuesObj;
                    for (Map.Entry<String, Object> entry : issuesMap.entrySet()) {
                        Object val = entry.getValue();
                        if (val instanceof Map) {
                            @SuppressWarnings("unchecked")
                            Map<String, Object> issueData = (Map<String, Object>) val;

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
                                        bi.setResolvedInCommitHash(request.getCommitHash());
                                        branchIssueRepository.save(bi);

                                        CodeAnalysisIssue cai = bi.getCodeAnalysisIssue();
                                        cai.setResolved(true);
                                        codeAnalysisIssueRepository.save(cai);
                                        log.info("Marked branch issue {} as resolved (commit: {})",
                                                actualIssueId,
                                                request.getCommitHash());
                                    }
                                }
                            }
                        }
                    }
                }

                branch.updateIssueCounts();
                if(project.getDefaultBranch() == null) {
                    project.setDefaultBranch(branch);
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

    /**
     * Index repository in RAG pipeline after branch analysis.
     *
     * For first-time analysis with archive: index full repository
     * For subsequent analyses: update changed files only
     */
    //TODO: MVP2, rag pipelines
//    private void indexRepositoryInRAG(BranchProcessRequest request, Project project, Set<String> changedFiles) {
//        try {
//            String projectWorkspace = project.getWorkspace().getName();
//            String projectNamespace = project.getName();
//            String branch = request.getTargetBranchName();
//            String commit = request.getCommitHash();
//
//            boolean isFirstTimeIndex = !ragIndexTrackingService.isProjectIndexed(project);
//
//            if (request.getArchive() != null && request.getArchive().length > 0) {
//                Optional<String> ragLockKey = analysisLockService.acquireLock(
//                        project,
//                        branch,
//                        AnalysisLockType.RAG_INDEXING,
//                        commit,
//                        null
//                );
//
//                if (ragLockKey.isEmpty()) {
//                    log.warn("RAG indexing already in progress for project={}, branch={}",
//                            project.getId(), branch);
//                    return;
//                }
//
//                try {
//                    if (!ragIndexTrackingService.canStartIndexing(project)) {
//                        log.warn("Cannot start RAG indexing - another indexing operation in progress");
//                        return;
//                    }
//
//                    ragIndexTrackingService.markIndexingStarted(project, branch, commit);
//
//                    log.info("Indexing full repository from archive in RAG pipeline");
//                    Map<String, Object> result = ragIndexingService.indexFromArchive(
//                            request.getArchive(),
//                            projectWorkspace,
//                            projectNamespace,
//                            branch,
//                            commit
//                    );
//
//                    Integer filesIndexed = extractFilesIndexed(result);
//                    ragIndexTrackingService.markIndexingCompleted(project, branch, commit, filesIndexed);
//
//                    log.info("RAG full repository indexing completed: {}", result);
//                } catch (Exception e) {
//                    ragIndexTrackingService.markIndexingFailed(project, e.getMessage());
//                    log.error("RAG indexing failed", e);
//                    throw e;
//                } finally {
//                    analysisLockService.releaseLock(ragLockKey.get());
//                }
//            } else if (!changedFiles.isEmpty() && ragIndexTrackingService.isProjectIndexed(project)) {
//                Optional<String> ragLockKey = analysisLockService.acquireLock(
//                        project,
//                        branch,
//                        AnalysisLockType.RAG_INDEXING,
//                        commit,
//                        null
//                );
//
//                if (ragLockKey.isEmpty()) {
//                    log.warn("RAG update already in progress for project={}, branch={}",
//                            project.getId(), branch);
//                    return;
//                }
//
//                try {
//                    ragIndexTrackingService.markUpdatingStarted(project, branch, commit);
//
//                    log.info("Updating {} changed files in RAG index", changedFiles.size());
//                    log.warn("Incremental RAG indexing not yet implemented - requires file content access");
//
//                    ragIndexTrackingService.markUpdatingCompleted(project, branch, commit, null);
//                } catch (Exception e) {
//                    ragIndexTrackingService.markIndexingFailed(project, e.getMessage());
//                    log.error("RAG update failed", e);
//                    throw e;
//                } finally {
//                    analysisLockService.releaseLock(ragLockKey.get());
//                }
//            } else if (!isFirstTimeIndex && (request.getArchive() == null || request.getArchive().length == 0)) {
//                log.info("Skipping RAG indexing - project already indexed and no archive provided");
//            }
//        } catch (Exception e) {
//            log.warn("RAG indexing failed (non-critical): {}", e.getMessage());
//        }
//    }
    //TODO: MVP2, rag pipelines
//    private Integer extractFilesIndexed(Map<String, Object> result) {
//        if (result == null) {
//            return null;
//        }
//        Object filesIndexed = result.get("files_indexed");
//        if (filesIndexed instanceof Number) {
//            return ((Number) filesIndexed).intValue();
//        }
//        return null;
//    }
}
