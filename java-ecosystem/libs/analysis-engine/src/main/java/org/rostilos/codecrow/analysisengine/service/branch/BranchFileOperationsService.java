package org.rostilos.codecrow.analysisengine.service.branch;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.branch.BranchFile;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchFileRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.service.FileSnapshotService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.stream.Collectors;

/**
 * Manages branch-level file operations: archive downloads, branch file
 * record updates, file snapshot persistence, and branch entity CRUD.
 */
@Service
public class BranchFileOperationsService {

    private static final Logger log = LoggerFactory.getLogger(BranchFileOperationsService.class);

    private final BranchFileRepository branchFileRepository;
    private final BranchRepository branchRepository;
    private final BranchIssueRepository branchIssueRepository;
    private final CodeAnalysisIssueRepository codeAnalysisIssueRepository;
    private final CodeAnalysisRepository codeAnalysisRepository;
    private final VcsServiceFactory vcsServiceFactory;
    private final VcsClientProvider vcsClientProvider;
    private final FileSnapshotService fileSnapshotService;
    private final BranchArchiveService branchArchiveService;

    public BranchFileOperationsService(
            BranchFileRepository branchFileRepository,
            BranchRepository branchRepository,
            BranchIssueRepository branchIssueRepository,
            CodeAnalysisIssueRepository codeAnalysisIssueRepository,
            CodeAnalysisRepository codeAnalysisRepository,
            VcsServiceFactory vcsServiceFactory,
            VcsClientProvider vcsClientProvider,
            FileSnapshotService fileSnapshotService,
            BranchArchiveService branchArchiveService) {
        this.branchFileRepository = branchFileRepository;
        this.branchRepository = branchRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.codeAnalysisIssueRepository = codeAnalysisIssueRepository;
        this.codeAnalysisRepository = codeAnalysisRepository;
        this.vcsServiceFactory = vcsServiceFactory;
        this.vcsClientProvider = vcsClientProvider;
        this.fileSnapshotService = fileSnapshotService;
        this.branchArchiveService = branchArchiveService;
    }

    // ────────────────────────── Archive download ──────────────────────────────

    /**
     * Downloads the branch archive and extracts the specified files into a map.
     * <p>
     * Replaces per-file VCS API calls with a single archive download,
     * avoiding rate-limiting issues (e.g. Bitbucket HTTP 429).
     * Returns an empty map on failure — callers must handle graceful fallback.
     */
    public Map<String, String> downloadBranchArchive(VcsRepoInfoImpl vcsRepoInfoImpl, String branchOrCommit,
                                                     Set<String> neededFiles) {
        try {
            return branchArchiveService.downloadAndExtractFiles(
                    vcsRepoInfoImpl.vcsConnection(), vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                    branchOrCommit, neededFiles);
        } catch (Exception e) {
            log.warn("Failed to download branch archive — will fall back to per-file API calls: {}",
                    e.getMessage());
            return Collections.emptyMap();
        }
    }

    // ───────────────────── Branch file record management ──────────────────────

    /**
     * Updates branch file records for changed files.
     * <p>
     * When {@code archiveContents} is non-empty, file existence is determined
     * from the archive map instead of per-file API calls (avoids rate-limiting).
     *
     * @param archiveContents pre-downloaded archive contents (filePath → content).
     *                        May be empty — in that case, falls back to per-file API.
     * @return the set of file paths confirmed to exist in the branch
     */
    public Set<String> updateBranchFiles(Set<String> changedFiles, Project project,
                                         String branchName, Map<String, String> archiveContents) {
        Set<String> filesExistingInBranch = new HashSet<>();
        boolean useArchive = archiveContents != null && !archiveContents.isEmpty();

        Branch branchEntity = branchRepository
                .findByProjectIdAndBranchName(project.getId(), branchName).orElse(null);

        // Fallback VCS clients — only initialized when archive is not available
        VcsOperationsService operationsService = null;
        OkHttpClient client = null;
        String workspace = null;
        String repoSlug = null;
        if (!useArchive) {
            var vcsRepoInfo = project.getEffectiveVcsRepoInfo();
            if (vcsRepoInfo != null && vcsRepoInfo.getVcsConnection() != null) {
                EVcsProvider provider = vcsRepoInfo.getVcsConnection().getProviderType();
                operationsService = vcsServiceFactory.getOperationsService(provider);
                client = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
                workspace = vcsRepoInfo.getRepoWorkspace();
                repoSlug = vcsRepoInfo.getRepoSlug();
            }
        }

        for (String filePath : changedFiles) {
            boolean fileExists = resolveFileExistence(
                    filePath, branchName, useArchive, archiveContents,
                    operationsService, client, workspace, repoSlug);

            if (!fileExists) {
                log.debug("Skipping file {} - does not exist in branch {}", filePath, branchName);
                continue;
            }
            filesExistingInBranch.add(filePath);

            long unresolvedCount = countUnresolvedIssues(branchEntity, project, branchName, filePath);
            persistBranchFile(project, branchName, filePath, branchEntity, (int) unresolvedCount);
        }
        return filesExistingInBranch;
    }

    // ──────────────────── Branch entity CRUD ─────────────────────────────────

    /**
     * Creates or updates the branch entity for the given project/request.
     */
    public Branch createOrUpdateProjectBranch(Project project, BranchProcessRequest request,
                                              Branch existingBranch) {
        Branch branch;
        if (existingBranch != null) {
            branch = existingBranch;
        } else {
            branch = new Branch();
            branch.setProject(project);
            branch.setBranchName(request.getTargetBranchName());
        }
        branch.setCommitHash(request.getCommitHash());
        return branchRepository.save(branch);
    }

    // ──────────────────── File snapshot updates ──────────────────────────────

    /**
     * Update file snapshots at the <b>branch</b> level using
     * pre-downloaded archive contents.
     * <p>
     * Snapshots are stored keyed on {@code (branch_id, file_path)} so that
     * each branch has exactly one snapshot per file, always pointing to the
     * latest content. <b>Analysis-level snapshots remain immutable</b> — they
     * preserve the file content at the time each issue was originally detected,
     * which is critical for the Source Context viewer.
     * <p>
     * When {@code archiveContents} is non-empty, file content is read from the
     * map directly (no API calls). When empty, falls back to per-file VCS API.
     */
    public void updateFileSnapshotsForBranch(Set<String> existingFiles, Project project,
                                             BranchProcessRequest request,
                                             Map<String, String> archiveContents) {
        if (existingFiles.isEmpty()) return;

        try {
            Optional<Branch> branchOpt = branchRepository
                    .findByProjectIdAndBranchName(project.getId(), request.getTargetBranchName());
            if (branchOpt.isEmpty()) {
                log.debug("No branch entity found for {} — skipping snapshot update",
                        request.getTargetBranchName());
                return;
            }
            Branch branch = branchOpt.get();

            Map<String, String> fileContents = buildFileContentsMap(
                    existingFiles, project, request, archiveContents);

            if (!fileContents.isEmpty()) {
                int updated = fileSnapshotService.persistSnapshotsForBranch(
                        branch, fileContents, request.getCommitHash());
                if (updated > 0) {
                    log.info("Updated {} branch-level file snapshots for branch {} (commit: {})",
                            updated, request.getTargetBranchName(),
                            request.getCommitHash().substring(0, Math.min(7, request.getCommitHash().length())));
                }
            }
        } catch (Exception e) {
            log.warn("Failed to update file snapshots for branch {} (non-critical): {}",
                    request.getTargetBranchName(), e.getMessage());
        }
    }

    // ──────────────────── Query helpers ──────────────────────────────────────

    /**
     * Returns the set of file paths tracked for a given branch.
     */
    public Set<String> getBranchFilePaths(Long projectId, String branchName) {
        return branchFileRepository.findByProjectIdAndBranchName(projectId, branchName)
                .stream()
                .map(BranchFile::getFilePath)
                .collect(Collectors.toSet());
    }

    // ──────────────────── Private helpers ────────────────────────────────────

    private boolean resolveFileExistence(String filePath, String branchName,
                                         boolean useArchive, Map<String, String> archiveContents,
                                         VcsOperationsService operationsService, OkHttpClient client,
                                         String workspace, String repoSlug) {
        if (useArchive) {
            boolean exists = archiveContents.containsKey(filePath);
            if (!exists) {
                log.debug("File {} not found in archive — treating as deleted from branch {}",
                        filePath, branchName);
            }
            return exists;
        }
        try {
            return operationsService.checkFileExistsInBranch(
                    client, workspace, repoSlug, branchName, filePath);
        } catch (Exception e) {
            log.warn("Failed to check file existence for {} in branch {}: {}. Proceeding anyway.",
                    filePath, branchName, e.getMessage());
            return true;
        }
    }

    private long countUnresolvedIssues(Branch branchEntity, Project project,
                                       String branchName, String filePath) {
        if (branchEntity != null) {
            return branchIssueRepository
                    .findUnresolvedByBranchIdAndFilePath(branchEntity.getId(), filePath)
                    .size();
        }
        // Fallback: count from CodeAnalysisIssue (branch entity not yet created)
        List<CodeAnalysisIssue> relatedIssues = codeAnalysisIssueRepository
                .findByProjectIdAndFilePath(project.getId(), filePath);
        return relatedIssues.stream()
                .filter(i -> !i.isResolved())
                .filter(i -> {
                    CodeAnalysis a = i.getAnalysis();
                    return a != null && (branchName.equals(a.getBranchName()) ||
                            branchName.equals(a.getSourceBranchName()));
                })
                .count();
    }

    private void persistBranchFile(Project project, String branchName, String filePath,
                                   Branch branchEntity, int unresolvedCount) {
        Optional<BranchFile> existingOpt = branchFileRepository
                .findByProjectIdAndBranchNameAndFilePath(project.getId(), branchName, filePath);
        if (existingOpt.isPresent()) {
            BranchFile branchFile = existingOpt.get();
            branchFile.setIssueCount(unresolvedCount);
            if (branchFile.getBranch() == null && branchEntity != null) {
                branchFile.setBranch(branchEntity);
            }
            branchFileRepository.save(branchFile);
        } else {
            BranchFile branchFile = new BranchFile();
            branchFile.setProject(project);
            branchFile.setBranchName(branchName);
            branchFile.setFilePath(filePath);
            branchFile.setIssueCount(unresolvedCount);
            if (branchEntity != null) {
                branchFile.setBranch(branchEntity);
            }
            branchFileRepository.save(branchFile);
        }
    }

    private Map<String, String> buildFileContentsMap(Set<String> existingFiles, Project project,
                                                     BranchProcessRequest request,
                                                     Map<String, String> archiveContents) {
        Map<String, String> fileContents = new LinkedHashMap<>();
        boolean useArchive = archiveContents != null && !archiveContents.isEmpty();

        if (useArchive) {
            for (String filePath : existingFiles) {
                String content = archiveContents.get(filePath);
                if (content != null) {
                    fileContents.put(filePath, content);
                }
            }
        } else {
            var vcsRepoInfo = project.getEffectiveVcsRepoInfo();
            if (vcsRepoInfo == null || vcsRepoInfo.getVcsConnection() == null) return fileContents;

            EVcsProvider provider = vcsRepoInfo.getVcsConnection().getProviderType();
            VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());

            for (String filePath : existingFiles) {
                try {
                    String content = operationsService.getFileContent(
                            client, vcsRepoInfo.getRepoWorkspace(), vcsRepoInfo.getRepoSlug(),
                            request.getCommitHash(), filePath);
                    if (content != null) {
                        fileContents.put(filePath, content);
                    }
                } catch (Exception e) {
                    log.debug("Could not fetch content for {} (snapshot update): {}", filePath, e.getMessage());
                }
            }
        }
        return fileContents;
    }
}
