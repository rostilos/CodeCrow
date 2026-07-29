package org.rostilos.codecrow.analysisengine.service.branch;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.VcsRepoInfoImpl;
import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.analysisengine.service.VcsFileRetrievalPolicy;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.filecontent.model.BranchFile;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.filecontent.persistence.BranchFileRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
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
    private final VcsFileRetrievalPolicy fileRetrievalPolicy;

    public BranchFileOperationsService(
            BranchFileRepository branchFileRepository,
            BranchRepository branchRepository,
            BranchIssueRepository branchIssueRepository,
            CodeAnalysisIssueRepository codeAnalysisIssueRepository,
            CodeAnalysisRepository codeAnalysisRepository,
            VcsServiceFactory vcsServiceFactory,
            VcsClientProvider vcsClientProvider,
            FileSnapshotService fileSnapshotService,
            BranchArchiveService branchArchiveService,
            VcsFileRetrievalPolicy fileRetrievalPolicy) {
        this.branchFileRepository = branchFileRepository;
        this.branchRepository = branchRepository;
        this.branchIssueRepository = branchIssueRepository;
        this.codeAnalysisIssueRepository = codeAnalysisIssueRepository;
        this.codeAnalysisRepository = codeAnalysisRepository;
        this.vcsServiceFactory = vcsServiceFactory;
        this.vcsClientProvider = vcsClientProvider;
        this.fileSnapshotService = fileSnapshotService;
        this.branchArchiveService = branchArchiveService;
        this.fileRetrievalPolicy = fileRetrievalPolicy;
    }

    // ────────────────────────── Archive download ──────────────────────────────

    /**
     * Downloads the branch archive and extracts the specified files into a map.
     * <p>
     * Replaces per-file VCS API calls with a single archive download,
     * avoiding rate-limiting issues (e.g. Bitbucket HTTP 429).
     * Legacy content-only view. New orchestration code must use
     * {@link #downloadBranchFileSnapshot(VcsRepoInfoImpl, String, Set)} so an
     * unavailable archive cannot be confused with a successful empty snapshot.
     */
    public Map<String, String> downloadBranchArchive(VcsRepoInfoImpl vcsRepoInfoImpl, String branchOrCommit,
                                                     Set<String> neededFiles) {
        return downloadBranchFileSnapshot(vcsRepoInfoImpl, branchOrCommit, neededFiles).contents();
    }

    /**
     * Downloads one authoritative repository snapshot for a bounded file set.
     * An unavailable archive is represented explicitly so downstream stages do
     * not mistake an acquisition failure for an empty repository.
     */
    public BranchFileSnapshot downloadBranchFileSnapshot(
            VcsRepoInfoImpl vcsRepoInfoImpl,
            String branchOrCommit,
            Set<String> neededFiles) {
        Set<String> requestedFiles = neededFiles != null
                ? new LinkedHashSet<>(neededFiles)
                : Collections.emptySet();
        try {
            BranchArchiveService.ArchiveSnapshot snapshot = branchArchiveService.downloadSnapshot(
                    vcsRepoInfoImpl.vcsConnection(), vcsRepoInfoImpl.workspace(), vcsRepoInfoImpl.repoSlug(),
                    branchOrCommit, requestedFiles);
            return BranchFileSnapshot.fromArchive(snapshot);
        } catch (Exception e) {
            boolean allowPerFileFallback =
                    fileRetrievalPolicy.allowPerFileFallback(requestedFiles.size());
            log.warn("Failed to download branch archive for {} requested files: {}. {}",
                    requestedFiles.size(), e.getMessage(),
                    allowPerFileFallback
                            ? "A bounded per-file fallback remains available"
                            : "Per-file fallback is disabled above the shared archive threshold");
            return BranchFileSnapshot.unavailable(allowPerFileFallback, e.getMessage());
        }
    }

    // ───────────────────── Branch file record management ──────────────────────

    /**
     * Updates branch file records for changed files.
     * <p>
     * Prefer the {@link BranchFileSnapshot} overload. It keeps archive
     * acquisition state and path presence separate from extracted text.
     *
     * @param archiveContents pre-downloaded archive contents (filePath → content).
     *                        May be empty; retained only for legacy callers.
     * @return the set of file paths confirmed to exist in the branch
     */
    public Set<String> updateBranchFiles(Set<String> changedFiles, Project project,
                                         String branchName, Map<String, String> archiveContents) {
        return updateBranchFiles(
                changedFiles, project, branchName, BranchFileSnapshot.legacy(archiveContents));
    }

    public Set<String> updateBranchFiles(Set<String> changedFiles, Project project,
                                         String branchName, BranchFileSnapshot fileSnapshot) {
        Set<String> filesExistingInBranch = new HashSet<>();
        BranchFileSnapshot snapshot = fileSnapshot != null
                ? fileSnapshot
                : BranchFileSnapshot.unavailable(true, "snapshot not provided");

        Branch branchEntity = branchRepository
                .findByProjectIdAndBranchName(project.getId(), branchName).orElse(null);

        // Resolve provider clients only when a bounded per-file fallback is
        // actually allowed. An authoritative archive snapshot (including its
        // binary/large-file presence set) must never trigger extra API calls.
        VcsOperationsService operationsService = null;
        OkHttpClient client = null;
        String workspace = null;
        String repoSlug = null;
        var vcsRepoInfo = project.getEffectiveVcsRepoInfo();
        if (!snapshot.archiveAvailable()
                && snapshot.allowPerFileFallback()
                && vcsRepoInfo != null
                && vcsRepoInfo.getVcsConnection() != null) {
            EVcsProvider provider = vcsRepoInfo.getVcsConnection().getProviderType();
            operationsService = vcsServiceFactory.getOperationsService(provider);
            client = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());
            workspace = vcsRepoInfo.getRepoWorkspace();
            repoSlug = vcsRepoInfo.getRepoSlug();
        }

        for (String filePath : changedFiles) {
            boolean fileExists = resolveFileExistence(
                    filePath, branchName, snapshot,
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
     * Prefer the {@link BranchFileSnapshot} overload so optional snapshot
     * persistence follows the same bounded acquisition decision as existence
     * reconciliation.
     */
    public void updateFileSnapshotsForBranch(Set<String> existingFiles, Project project,
                                             BranchProcessRequest request,
                                             Map<String, String> archiveContents) {
        updateFileSnapshotsForBranch(
                existingFiles, project, request, BranchFileSnapshot.legacy(archiveContents));
    }

    public void updateFileSnapshotsForBranch(Set<String> existingFiles, Project project,
                                             BranchProcessRequest request,
                                             BranchFileSnapshot fileSnapshot) {
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
                    existingFiles, project, request, fileSnapshot);

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
                                         BranchFileSnapshot snapshot,
                                         VcsOperationsService operationsService, OkHttpClient client,
                                         String workspace, String repoSlug) {
        if (snapshot.archiveAvailable()) {
            return snapshot.presentFiles().contains(filePath);
        }
        if (!snapshot.allowContentApiFallback()) {
            log.debug("Per-file fallback unavailable for {} in branch {} — "
                    + "assuming it exists (fail-open)", filePath, branchName);
            return true;
        }
        if (operationsService == null || client == null) {
            log.debug("No VCS fallback available for {} — assuming it exists in branch {}",
                    filePath, branchName);
            return true;
        }
        try {
            return operationsService.checkFileExistsInBranch(
                    client, workspace, repoSlug, branchName, filePath);
        } catch (Exception e) {
            snapshot.stopPerFileFallback();
            log.warn("File-existence fallback stopped after provider failure for {} in branch {}: {}. "
                            + "Remaining files will be treated as existing without additional VCS calls.",
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
                                                     BranchFileSnapshot fileSnapshot) {
        Map<String, String> fileContents = new LinkedHashMap<>();
        BranchFileSnapshot snapshot = fileSnapshot != null
                ? fileSnapshot
                : BranchFileSnapshot.unavailable(true, "snapshot not provided");

        if (snapshot.archiveAvailable()) {
            for (String filePath : existingFiles) {
                String content = snapshot.contents().get(filePath);
                if (content != null) {
                    fileContents.put(filePath, content);
                }
            }
        } else if (snapshot.allowContentApiFallback()) {
            var vcsRepoInfo = project.getEffectiveVcsRepoInfo();
            if (vcsRepoInfo == null || vcsRepoInfo.getVcsConnection() == null) return fileContents;

            EVcsProvider provider = vcsRepoInfo.getVcsConnection().getProviderType();
            VcsOperationsService operationsService = vcsServiceFactory.getOperationsService(provider);
            OkHttpClient client = vcsClientProvider.getHttpClient(vcsRepoInfo.getVcsConnection());

            for (String filePath : existingFiles) {
                if (!snapshot.allowContentApiFallback()) {
                    break;
                }
                try {
                    String content = operationsService.getFileContent(
                            client, vcsRepoInfo.getRepoWorkspace(), vcsRepoInfo.getRepoSlug(),
                            request.getCommitHash(), filePath);
                    if (content != null) {
                        fileContents.put(filePath, content);
                    }
                } catch (Exception e) {
                    snapshot.stopPerFileFallback();
                    log.warn("Stopping per-file snapshot fallback after provider failure for {}: {}",
                            filePath, e.getMessage());
                    break;
                }
            }
        } else {
            log.warn("Skipping branch snapshot content fallback for {} files because the archive "
                            + "was unavailable and the shared per-file threshold was exceeded",
                    existingFiles.size());
        }
        return fileContents;
    }

    public record BranchFileSnapshot(
            Map<String, String> contents,
            Set<String> presentFiles,
            boolean archiveAvailable,
            boolean allowPerFileFallback,
            String diagnostic,
            AtomicBoolean providerUnavailable
    ) {
        public BranchFileSnapshot {
            contents = Collections.unmodifiableMap(new LinkedHashMap<>(
                    contents != null ? contents : Collections.emptyMap()));
            presentFiles = Collections.unmodifiableSet(new LinkedHashSet<>(
                    presentFiles != null ? presentFiles : Collections.emptySet()));
            providerUnavailable = providerUnavailable != null
                    ? providerUnavailable
                    : new AtomicBoolean(false);
        }

        public static BranchFileSnapshot fromArchive(
                BranchArchiveService.ArchiveSnapshot snapshot) {
            return new BranchFileSnapshot(
                    snapshot.contents(), snapshot.presentFiles(), true, false, null,
                    new AtomicBoolean(false));
        }

        public static BranchFileSnapshot unavailable(
                boolean allowPerFileFallback, String diagnostic) {
            return new BranchFileSnapshot(
                    Collections.emptyMap(), Collections.emptySet(), false,
                    allowPerFileFallback, diagnostic, new AtomicBoolean(false));
        }

        public static BranchFileSnapshot legacy(Map<String, String> contents) {
            Map<String, String> safeContents =
                    contents != null ? contents : Collections.emptyMap();
            if (safeContents.isEmpty()) {
                return unavailable(true, "legacy empty archive contents");
            }
            return new BranchFileSnapshot(
                    safeContents, safeContents.keySet(), true, false, null,
                    new AtomicBoolean(false));
        }

        public boolean allowContentApiFallback() {
            return !archiveAvailable
                    && allowPerFileFallback
                    && !providerUnavailable.get();
        }

        public void stopPerFileFallback() {
            providerUnavailable.set(true);
        }
    }
}
