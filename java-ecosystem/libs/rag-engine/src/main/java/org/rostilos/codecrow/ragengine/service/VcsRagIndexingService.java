package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLogLevel;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.service.AnalysisJobService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.FileVisitResult;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.PosixFilePermission;
import java.util.Comparator;
import java.util.EnumSet;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.rostilos.codecrow.ragengine.source.RepositorySourceTreeIdentity;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.scheduling.annotation.Async;

@Service
public class VcsRagIndexingService {
    private static final Logger log = LoggerFactory.getLogger(VcsRagIndexingService.class);

    private final ProjectRepository projectRepository;
    private final VcsClientProvider vcsClientProvider;
    private final RagIndexingService ragIndexingService;
    private final RagIndexTrackingService ragIndexTrackingService;
    private final AnalysisLockService analysisLockService;
    private final AnalysisJobService jobService;
    private final RedisQueueService queueService;
    private final ObjectMapper objectMapper;

    @Lazy
    @Autowired
    private VcsRagIndexingService self;

    @Value("${codecrow.rag.api.enabled:true}")
    private boolean ragApiEnabled;

    @Value("${codecrow.rag.queue.inactivity-timeout-minutes:15}")
    private long ragQueueInactivityTimeoutMinutes;

    @Value("${codecrow.rag.queue.lock-lease-minutes:30}")
    private int ragQueueLockLeaseMinutes;

    public VcsRagIndexingService(
            ProjectRepository projectRepository,
            VcsClientProvider vcsClientProvider,
            RagIndexingService ragIndexingService,
            RagIndexTrackingService ragIndexTrackingService,
            AnalysisLockService analysisLockService,
            AnalysisJobService jobService,
            RedisQueueService queueService,
            ObjectMapper objectMapper) {
        this.projectRepository = projectRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.ragIndexingService = ragIndexingService;
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.analysisLockService = analysisLockService;
        this.jobService = jobService;
        this.queueService = queueService;
        this.objectMapper = objectMapper;
    }

    public Map<String, Object> indexProjectFromVcs(
            ProjectDTO authProject,
            String requestBranch,
            Consumer<Map<String, Object>> messageConsumer) {
        if (!ragApiEnabled) {
            log.warn("RAG API is disabled, skipping indexing");
            return Map.of("status", "skipped", "message", "RAG API is disabled");
        }

        if (!ragIndexingService.isAvailable()) {
            log.warn("RAG pipeline is not available");
            return Map.of("status", "error", "message", "RAG pipeline service is not available");
        }

        Project project = projectRepository.findByIdWithFullDetails(authProject.id())
                .orElseThrow(() -> new NoSuchElementException("Project not found: " + authProject.id()));

        ProjectConfig config = project.getConfiguration();
        if (config == null || config.ragConfig() == null || !config.ragConfig().enabled()) {
            log.info("RAG is not enabled for project: {}", project.getName());
            return Map.of("status", "skipped", "message", "RAG is not enabled for this project");
        }

        VcsConnection vcsConnection;
        String workspaceSlug;
        String repoSlug;

        // Use unified method to get VCS info
        var vcsInfo = project.getEffectiveVcsRepoInfo();
        if (vcsInfo != null && vcsInfo.getVcsConnection() != null) {
            vcsConnection = vcsInfo.getVcsConnection();
            workspaceSlug = vcsInfo.getRepoWorkspace();
            repoSlug = vcsInfo.getRepoSlug();
        } else {
            log.warn("Project {} has no VCS binding", project.getName());
            return Map.of("status", "error", "message", "Project has no VCS connection");
        }

        String branch = determineBranch(requestBranch, config, project);
        if (branch == null) {
            String message = "No RAG indexing branch is configured for project: " + project.getName();
            log.warn(message);
            return Map.of("status", "error", "message", message);
        }

        // Check if indexing can start BEFORE creating job to avoid orphan "failed" jobs
        if (!ragIndexTrackingService.canStartIndexing(project)) {
            log.warn("RAG indexing already in progress for project: {}", project.getName());
            return Map.of("status", "locked", "message", "RAG indexing is already in progress");
        }

        // Try to acquire lock BEFORE creating job - this is the authoritative check
        Optional<String> lockKey = analysisLockService.acquireLock(
                project, branch, AnalysisLockType.RAG_INDEXING);

        if (lockKey.isEmpty()) {
            log.warn("Failed to acquire RAG indexing lock for project: {} (another process holds the lock)",
                    project.getName());
            return Map.of("status", "locked", "message",
                    "RAG indexing is already in progress (lock held by another process)");
        }

        try {
            // Now that we have the lock, create and start the job
            Job job = jobService.createRagIndexJob(project, null);
            if (job != null) {
                jobService.startJob(job);
                jobService.logToJob(job, JobLogLevel.INFO, "init",
                        "Starting RAG indexing for branch: " + branch);
            }

            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "init",
                    "message", "Starting RAG indexing for branch: " + branch));

            return performIndexing(project, vcsConnection, workspaceSlug, repoSlug, branch, config, messageConsumer,
                    job, lockKey.get());
        } catch (Exception e) {
            analysisLockService.releaseLock(lockKey.get());
            throw e;
        }
    }

    private Map<String, Object> performIndexing(
            Project project,
            VcsConnection vcsConnection,
            String workspaceSlug,
            String repoSlug,
            String branch,
            ProjectConfig config,
            Consumer<Map<String, Object>> messageConsumer,
            Job job,
            String lockKey) {
        try {
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);

            String vcsMsg = "Fetching latest commit info...";
            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "vcs",
                    "message", vcsMsg));
            jobService.logToJob(job, JobLogLevel.INFO, "vcs", vcsMsg);

            String commitHash = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, branch);
            if (commitHash == null) {
                String errorMsg = "Failed to get latest commit for branch: " + branch;
                jobService.failJob(job, errorMsg);
                return Map.of("status", "error", "message", errorMsg);
            }

            ragIndexTrackingService.markIndexingStarted(
                    project, branch, commitHash, job != null ? job.getId() : null);

            String downloadMsg = "Downloading repository archive...";
            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "download",
                    "message", downloadMsg));
            jobService.logToJob(job, JobLogLevel.INFO, "download", downloadMsg);

            Path tempArchiveFile = Files.createTempFile("codecrow-archive-", ".zip");
            try {
                // Index exactly the commit recorded above. Downloading the
                // mutable branch here could race a push and mislabel different
                // archive bytes with the earlier commit hash.
                long archiveSize = vcsClient.downloadRepositoryArchiveToFile(workspaceSlug, repoSlug, commitHash,
                        tempArchiveFile);
                log.info("Downloaded archive: {} bytes for {}/{}", archiveSize, workspaceSlug, repoSlug);

                String downloadedMsg = "Downloaded " + formatBytes(archiveSize);
                messageConsumer.accept(Map.of(
                        "type", "progress",
                        "stage", "download",
                        "message", downloadedMsg));
                jobService.logToJob(job, JobLogLevel.INFO, "download", downloadedMsg);

                String indexingMsg = "Indexing repository in RAG pipeline...";
                messageConsumer.accept(Map.of(
                        "type", "progress",
                        "stage", "indexing",
                        "message", indexingMsg));
                jobService.logToJob(job, JobLogLevel.INFO, "indexing", indexingMsg);

                var excludePatterns = config.ragConfig() != null ? config.ragConfig().excludePatterns() : null;
                var includePatterns = config.ragConfig() != null ? config.ragConfig().includePatterns() : null;
                Path tempDir = Files.createTempDirectory("codecrow-rag-");
                boolean ownershipTransferred = false;

                try {
                    // Extract the downloaded archive locally
                    jobService.logToJob(job, JobLogLevel.INFO, "extraction", "Extracting repository archive...");
                    extractArchiveFileAndCleanup(tempArchiveFile, tempDir);
                    String sourceTreeSha256 =
                            RepositorySourceTreeIdentity.sha256(tempDir);

                    if (includePatterns != null && !includePatterns.isEmpty()) {
                        log.info("Using {} include patterns from project config", includePatterns.size());
                        String includeMsg = "Including " + includePatterns.size() + " custom patterns";
                        messageConsumer.accept(Map.of("type", "progress", "stage", "indexing", "message", includeMsg));
                        jobService.logToJob(job, JobLogLevel.INFO, "indexing", includeMsg);
                    }
                    if (excludePatterns != null && !excludePatterns.isEmpty()) {
                        log.info("Using {} exclude patterns from project config", excludePatterns.size());
                        String excludeMsg = "Excluding " + excludePatterns.size() + " custom patterns";
                        messageConsumer.accept(Map.of("type", "progress", "stage", "indexing", "message", excludeMsg));
                        jobService.logToJob(job, JobLogLevel.INFO, "indexing", excludeMsg);
                    }

                    // Push job to Redis queue
                    String jobId = UUID.randomUUID().toString();
                    Map<String, Object> requestPayload = new java.util.LinkedHashMap<>();
                    requestPayload.put("repo_path", tempDir.toAbsolutePath().toString());
                    requestPayload.put("workspace", project.getWorkspace().getName());
                    requestPayload.put("project", project.getNamespace());
                    requestPayload.put("branch", branch);
                    requestPayload.put("commit", commitHash);
                    requestPayload.put("source_tree_sha256", sourceTreeSha256);
                    requestPayload.put("preserve_other_branches", config.ragConfig().isMultiBranchEnabled());
                    requestPayload.put("cleanup_repo_path", true);
                    requestPayload.put("include_patterns", includePatterns != null ? includePatterns : java.util.List.of());
                    requestPayload.put("exclude_patterns", excludePatterns != null ? excludePatterns : java.util.List.of());
                    var analysisProfile = config.analysisProfile();
                    if (analysisProfile.projectType() != null) {
                        requestPayload.put("project_type", analysisProfile.projectType());
                    }
                    if (analysisProfile.sourceRoot() != null) {
                        requestPayload.put("source_root", analysisProfile.sourceRoot());
                    }

                    Map<String, Object> jobPayload = Map.of(
                            "job_id", jobId,
                            "queued_at_epoch_ms", System.currentTimeMillis(),
                            "request", requestPayload);

                    String eventQueueKey = "codecrow:analysis:events:" + jobId;
                    String jobsQueueKey = "codecrow:queue:rag";
                    String serializedJobPayload = objectMapper.writeValueAsString(jobPayload);

                    queueService.leftPush(jobsQueueKey, serializedJobPayload);
                    ownershipTransferred = true;
                    queueService.setExpiry(eventQueueKey, 245); // ~ 4 hours + 5 mins buffer

                    jobService.logToJob(job, JobLogLevel.INFO, "indexing",
                            "Queued RAG indexing in Redis (Job ID: " + jobId + ")");

                    // Delegate the polling to a background executor thread, returning immediately
                    // to caller
                    self.pollRagIndexingJobAsync(jobId, eventQueueKey, project, branch, commitHash, tempDir, lockKey,
                            job, jobsQueueKey, serializedJobPayload);

                    return Map.of(
                            "status", "queued",
                            "message", "RAG indexing job queued in background",
                            "jobId", job != null ? job.getExternalId() : jobId,
                            "branch", branch,
                            "commitHash", commitHash);

                } catch (Exception fileExtractEx) { // Catch issues during extraction specifically
                    if (!ownershipTransferred) {
                        deleteDir(tempDir);
                    }
                    throw fileExtractEx;
                }

            } finally {
                try {
                    Files.deleteIfExists(tempArchiveFile);
                } catch (IOException e) {
                    log.warn("Failed to delete temporary archive file: {}", tempArchiveFile, e);
                }
            }

        } catch (Exception e) {
            log.error("RAG indexing failed for project {}", project.getName(), e);
            ragIndexTrackingService.markIndexingFailed(
                    project, e.getMessage(), job != null ? job.getId() : null);
            analysisLockService.releaseLock(lockKey);

            messageConsumer.accept(Map.of(
                    "type", "error",
                    "stage", "failed",
                    "message", "RAG indexing failed: " + e.getMessage()));

            if (job != null) {
                jobService.logToJob(job, JobLogLevel.ERROR, "error", "RAG indexing failed: " + e.getMessage());
                jobService.failJob(job, "RAG indexing failed: " + e.getMessage());
            }

            return Map.of("status", "error", "message", e.getMessage());
        }
    }

    /**
     * Extracts zip file to destination, uses zip commands if possible.
     */
    void extractArchiveFileAndCleanup(Path archiveFile, Path destDir) throws IOException {
        try {
            ProcessBuilder pb = new ProcessBuilder("unzip", "-q", "-o", archiveFile.toAbsolutePath().toString(), "-d",
                    destDir.toAbsolutePath().toString());
            Process p = pb.start();
            int exitCode = p.waitFor();
            if (exitCode != 0) {
                throw new IOException("Failed to extract repository archive, unzip exit code: " + exitCode);
            }

            if (normalizeSingleArchiveRoot(destDir)) {
                log.info("Normalized single VCS archive root before RAG plugin detection");
            }

            prepareTransferredWorkspacePermissions(destDir);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("Extraction interrupted", e);
        }
    }

    /**
     * Prepare a shared-volume workspace for ownership transfer to the RAG
     * consumer, which runs under a different container UID.
     *
     * <p>Files only need cross-user read access. Directories also need
     * cross-user write access so the consumer that owns the queued request can
     * remove the workspace after indexing. File write bits are not broadened.</p>
     */
    static void prepareTransferredWorkspacePermissions(Path destination) throws IOException {
        Set<PosixFilePermission> directoryPermissions = EnumSet.allOf(PosixFilePermission.class);
        Files.walkFileTree(destination, new SimpleFileVisitor<>() {
            @Override
            public FileVisitResult preVisitDirectory(Path directory, BasicFileAttributes attributes)
                    throws IOException {
                Files.setPosixFilePermissions(directory, directoryPermissions);
                return FileVisitResult.CONTINUE;
            }

            @Override
            public FileVisitResult visitFile(Path file, BasicFileAttributes attributes)
                    throws IOException {
                if (!Files.isSymbolicLink(file)) {
                    Set<PosixFilePermission> permissions =
                            EnumSet.copyOf(Files.getPosixFilePermissions(file));
                    permissions.add(PosixFilePermission.GROUP_READ);
                    permissions.add(PosixFilePermission.OTHERS_READ);
                    Files.setPosixFilePermissions(file, permissions);
                }
                return FileVisitResult.CONTINUE;
            }
        });
    }

    /**
     * Provider archives wrap repository files in one synthetic top-level
     * directory. Move that directory's contents to the owned workspace root so
     * every downstream consumer sees repository-relative paths from the start.
     *
     * <p>This normalization is provider-neutral and happens before plugin
     * selection, include/exclude matching, architecture extraction, and
     * embedding. Archives that do not have exactly one directory root are left
     * unchanged.</p>
     */
    static boolean normalizeSingleArchiveRoot(Path destination) throws IOException {
        java.util.List<Path> roots;
        try (var entries = Files.list(destination)) {
            roots = entries.toList();
        }
        if (roots.size() != 1) {
            return false;
        }

        Path wrapper = roots.get(0);
        if (Files.isSymbolicLink(wrapper)
                || !Files.isDirectory(wrapper, java.nio.file.LinkOption.NOFOLLOW_LINKS)) {
            return false;
        }

        java.util.List<Path> children;
        try (var entries = Files.list(wrapper)) {
            children = entries.toList();
        }
        for (Path child : children) {
            Path target = destination.resolve(child.getFileName().toString());
            if (target.equals(wrapper) || Files.exists(target)) {
                return false;
            }
        }
        for (Path child : children) {
            Files.move(child, destination.resolve(child.getFileName().toString()));
        }
        Files.delete(wrapper);
        return true;
    }

    private void deleteDir(Path dir) {
        try {
            if (Files.exists(dir)) {
                Files.walk(dir)
                        .sorted(Comparator.reverseOrder())
                        .map(Path::toFile)
                        .forEach(File::delete);
            }
        } catch (Exception e) {
            log.warn("Failed to delete temporary directory: {}", dir, e);
        }
    }

    @Async("webhookExecutor")
    public void pollRagIndexingJobAsync(
            String jobId, String eventQueueKey, Project project, String branch,
            String commitHash, Path tempDir, String lockKey, Job job,
            String jobsQueueKey, String serializedJobPayload) {
        log.info("Background thread started polling for RAG Job ID: {}", jobId);
        long lastActivityTime = System.currentTimeMillis();
        long inactivityTimeoutMinutes = Math.max(1L, ragQueueInactivityTimeoutMinutes);
        long inactivityTimeoutMillis = TimeUnit.MINUTES.toMillis(inactivityTimeoutMinutes);
        Integer filesIndexed = null;
        Integer chunkCount = null;
        boolean success = false;
        String errorMessage = null;
        String lastPersistedStatus = null;

        try {
            while (true) {
                if (System.currentTimeMillis() - lastActivityTime > inactivityTimeoutMillis) {
                    if (queueService.listContains(jobsQueueKey, serializedJobPayload)) {
                        // The exact job is still durably queued behind admitted
                        // work. Extend producer supervision and the analysis
                        // lock without pretending that indexing has started.
                        lastActivityTime = System.currentTimeMillis();
                        if (!analysisLockService.renewLock(lockKey, ragQueueLockLeaseMinutes)) {
                            errorMessage = "RAG indexing lost its analysis-lock lease while queued for Job: "
                                    + jobId;
                            break;
                        }
                        ragIndexTrackingService.markIndexingHeartbeat(
                                project, job != null ? job.getId() : null);
                        continue;
                    }
                    errorMessage = "RAG indexing produced no worker heartbeat for "
                            + inactivityTimeoutMinutes + " minutes for Job: " + jobId;
                    break;
                }

                String eventJson = queueService.rightPop(eventQueueKey, 5);
                if (eventJson == null) {
                    continue;
                }
                lastActivityTime = System.currentTimeMillis();
                if (!analysisLockService.renewLock(lockKey, ragQueueLockLeaseMinutes)) {
                    errorMessage = "RAG indexing lost its analysis-lock lease for Job: " + jobId;
                    break;
                }

                try {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> event = objectMapper.readValue(eventJson, Map.class);
                    Object type = event.get("type");

                    if ("status".equals(type)) {
                        // Redis activity keeps the lease alive; persist the same
                        // activity on the existing status row so long-running,
                        // healthy indexes do not look stalled to operators.
                        ragIndexTrackingService.markIndexingHeartbeat(
                                project, job != null ? job.getId() : null);

                        String state = String.valueOf(event.getOrDefault("stage",
                                event.getOrDefault("state", "indexing")));
                        String message = String.valueOf(event.getOrDefault(
                                "message", "RAG indexing is processing"));
                        String statusIdentity = state + "\u0000" + message;
                        if (job != null && !statusIdentity.equals(lastPersistedStatus)) {
                            jobService.logToJob(job, JobLogLevel.INFO, state, message, event);
                            lastPersistedStatus = statusIdentity;
                        }
                    }

                    if ("error".equals(type) || "failed".equals(type)) {
                        errorMessage = String.valueOf(event.get("message"));
                        break;
                    }

                    if ("final".equals(type) || "result".equals(type)) {
                        Object oRes = event.get("result");
                        if (oRes instanceof Map) {
                            @SuppressWarnings("unchecked")
                            Map<String, Object> res = (Map<String, Object>) oRes;
                            if (res.get("document_count") != null) {
                                filesIndexed = ((Number) res.get("document_count")).intValue();
                            }
                            if (res.get("chunk_count") != null) {
                                chunkCount = ((Number) res.get("chunk_count")).intValue();
                            }
                        }
                        success = true;
                        break;
                    }
                } catch (Exception ex) {
                    log.warn("Failed to process Redis event for RAG indexing {}: {}", jobId, ex.getMessage());
                }
            }
        } catch (Exception ex) {
            errorMessage = "Background polling interrupted: " + ex.getMessage();
        } finally {
            // The queued RAG consumer owns tempDir after enqueue and removes it only after
            // indexing has stopped. Producer-side cleanup here can delete files from a
            // healthy, long-running consumer when polling or Redis is interrupted.
            analysisLockService.releaseLock(lockKey);
            try {
                queueService.deleteKey(eventQueueKey);
            } catch (Exception ignored) {
            }

            // Update Job and Project Tracking
            if (success) {
                ragIndexTrackingService.markIndexingCompleted(
                        project,
                        branch,
                        commitHash,
                        filesIndexed,
                        chunkCount,
                        job != null ? job.getId() : null);
                String completeMsg = "RAG indexing completed successfully. Files indexed: "
                        + (filesIndexed != null ? filesIndexed : 0);
                if (job != null) {
                    jobService.logToJob(job, JobLogLevel.INFO, "complete", completeMsg);
                    jobService.completeJob(job, null);
                }
                log.info("RAG indexing completed for project {} branch {}: {} files", project.getName(), branch,
                        filesIndexed);
            } else {
                ragIndexTrackingService.markIndexingFailed(
                        project,
                        errorMessage != null ? errorMessage : "Unknown Error",
                        job != null ? job.getId() : null);
                if (job != null) {
                    jobService.logToJob(job, JobLogLevel.ERROR, "error", "RAG indexing failed: " + errorMessage);
                    jobService.failJob(job, "RAG indexing failed: " + errorMessage);
                }
                log.error("RAG indexing failed for project {}: {}", project.getName(), errorMessage);
            }
        }
    }

    private String determineBranch(String requestBranch, ProjectConfig config, Project project) {
        String explicitBranch = normalizedBranch(requestBranch);
        if (explicitBranch != null) {
            return explicitBranch;
        }

        if (config != null && config.ragConfig() != null) {
            String ragBranch = normalizedBranch(config.ragConfig().branch());
            if (ragBranch != null) {
                return ragBranch;
            }
        }

        if (config != null) {
            String configuredBranch = normalizedBranch(config.defaultBranch());
            if (configuredBranch != null) {
                return configuredBranch;
            }
        }

        if (project != null && project.getVcsRepoBinding() != null) {
            String repositoryBranch = normalizedBranch(project.getVcsRepoBinding().getDefaultBranch());
            if (repositoryBranch != null) {
                return repositoryBranch;
            }
        }

        if (project != null && project.getDefaultBranch() != null) {
            return normalizedBranch(project.getDefaultBranch().getBranchName());
        }

        return null;
    }

    private String normalizedBranch(String branch) {
        if (branch == null) {
            return null;
        }
        String normalized = branch.trim();
        return normalized.isEmpty() ? null : normalized;
    }

    private String formatBytes(long bytes) {
        if (bytes < 1024)
            return bytes + " B";
        if (bytes < 1024 * 1024)
            return String.format("%.1f KB", bytes / 1024.0);
        return String.format("%.1f MB", bytes / (1024.0 * 1024.0));
    }

    @Transactional(readOnly = true)
    public boolean shouldAutoIndex(Project project) {
        if (!ragApiEnabled) {
            return false;
        }

        ProjectConfig config = project.getConfiguration();
        if (config == null || config.ragConfig() == null || !config.ragConfig().enabled()) {
            return false;
        }

        Optional<RagIndexStatus> status = ragIndexTrackingService.getIndexStatus(project);
        if (status.isPresent()) {
            return false;
        }

        // Use unified hasVcsBinding() check
        return project.hasVcsBinding();
    }
}
