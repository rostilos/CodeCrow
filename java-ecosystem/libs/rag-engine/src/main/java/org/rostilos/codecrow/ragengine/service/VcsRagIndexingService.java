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
import java.util.Comparator;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.queue.RedisQueueService;
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

        String branch = determineBranch(requestBranch, config);

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

            ragIndexTrackingService.markIndexingStarted(project, branch, commitHash);

            String downloadMsg = "Downloading repository archive...";
            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "download",
                    "message", downloadMsg));
            jobService.logToJob(job, JobLogLevel.INFO, "download", downloadMsg);

            Path tempArchiveFile = Files.createTempFile("codecrow-archive-", ".zip");
            try {
                long archiveSize = vcsClient.downloadRepositoryArchiveToFile(workspaceSlug, repoSlug, branch,
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

                try {
                    // Extract the downloaded archive locally
                    jobService.logToJob(job, JobLogLevel.INFO, "extraction", "Extracting repository archive...");
                    extractArchiveFileAndCleanup(tempArchiveFile, tempDir);

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
                    Map<String, Object> requestPayload = Map.of(
                            "repo_path", tempDir.toAbsolutePath().toString(),
                            "workspace", project.getWorkspace().getName(),
                            "project", project.getNamespace(),
                            "branch", branch,
                            "commit", commitHash,
                            "include_patterns", includePatterns != null ? includePatterns : java.util.List.of(),
                            "exclude_patterns", excludePatterns != null ? excludePatterns : java.util.List.of());

                    Map<String, Object> jobPayload = Map.of(
                            "job_id", jobId,
                            "request", requestPayload);

                    String eventQueueKey = "codecrow:analysis:events:" + jobId;
                    String jobsQueueKey = "codecrow:queue:rag";

                    queueService.leftPush(jobsQueueKey, objectMapper.writeValueAsString(jobPayload));
                    queueService.setExpiry(eventQueueKey, 245); // ~ 4 hours + 5 mins buffer

                    jobService.logToJob(job, JobLogLevel.INFO, "indexing",
                            "Queued RAG indexing in Redis (Job ID: " + jobId + ")");

                    // Delegate the polling to a background executor thread, returning immediately
                    // to caller
                    self.pollRagIndexingJobAsync(jobId, eventQueueKey, project, branch, commitHash, tempDir, lockKey,
                            job);

                    return Map.of(
                            "status", "queued",
                            "message", "RAG indexing job queued in background",
                            "branch", branch,
                            "commitHash", commitHash);

                } catch (Exception fileExtractEx) { // Catch issues during extraction specifically
                    deleteDir(tempDir);
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
            ragIndexTrackingService.markIndexingFailed(project, e.getMessage());
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

            // Ensure the extracted files and the parent directory are readable by the
            // Python pipeline running as appuser
            ProcessBuilder chmodPb = new ProcessBuilder("chmod", "-R", "755", destDir.toAbsolutePath().toString());
            Process chmodP = chmodPb.start();
            int chmodExitCode = chmodP.waitFor();
            if (chmodExitCode != 0) {
                log.warn("Failed to set 755 permissions on extracted archive directory: {}", destDir);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("Extraction interrupted", e);
        }
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
            String commitHash, Path tempDir, String lockKey, Job job) {
        log.info("Background thread started polling for RAG Job ID: {}", jobId);
        long startTime = System.currentTimeMillis();
        long timeoutMillis = TimeUnit.HOURS.toMillis(4) + TimeUnit.MINUTES.toMillis(5);
        Integer filesIndexed = null;
        Integer chunkCount = null;
        boolean success = false;
        String errorMessage = null;

        try {
            while (true) {
                if (System.currentTimeMillis() - startTime > timeoutMillis) {
                    errorMessage = "RAG indexing timed out after 4 hours for Job: " + jobId;
                    break;
                }

                String eventJson = queueService.rightPop(eventQueueKey, 5);
                if (eventJson == null) {
                    continue;
                }

                try {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> event = objectMapper.readValue(eventJson, Map.class);
                    Object type = event.get("type");

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
                    log.warn("Failed to parse Redis event JSON for RAG indexing {}: {}", jobId, ex.getMessage());
                }
            }
        } catch (Exception ex) {
            errorMessage = "Background polling interrupted: " + ex.getMessage();
        } finally {
            // Cleanup
            deleteDir(tempDir);
            analysisLockService.releaseLock(lockKey);
            try {
                queueService.deleteKey(eventQueueKey);
            } catch (Exception ignored) {
            }

            // Update Job and Project Tracking
            if (success) {
                ragIndexTrackingService.markIndexingCompleted(project, branch, commitHash, filesIndexed, chunkCount);
                String completeMsg = "RAG indexing completed successfully. Files indexed: "
                        + (filesIndexed != null ? filesIndexed : 0);
                if (job != null) {
                    jobService.logToJob(job, JobLogLevel.INFO, "complete", completeMsg);
                    jobService.completeJob(job, null);
                }
                log.info("RAG indexing completed for project {} branch {}: {} files", project.getName(), branch,
                        filesIndexed);
            } else {
                ragIndexTrackingService.markIndexingFailed(project,
                        errorMessage != null ? errorMessage : "Unknown Error");
                if (job != null) {
                    jobService.logToJob(job, JobLogLevel.ERROR, "error", "RAG indexing failed: " + errorMessage);
                    jobService.failJob(job, "RAG indexing failed: " + errorMessage);
                }
                log.error("RAG indexing failed for project {}: {}", project.getName(), errorMessage);
            }
        }
    }

    private String determineBranch(String requestBranch, ProjectConfig config) {
        if (requestBranch != null && !requestBranch.isBlank()) {
            return requestBranch;
        }

        if (config != null && config.ragConfig() != null && config.ragConfig().branch() != null) {
            return config.ragConfig().branch();
        }

        if (config != null && config.defaultBranch() != null) {
            return config.defaultBranch();
        }

        return "main";
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
