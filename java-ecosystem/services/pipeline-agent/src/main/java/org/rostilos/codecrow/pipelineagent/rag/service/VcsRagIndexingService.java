package org.rostilos.codecrow.pipelineagent.rag.service;

import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLogLevel;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.pipelineagent.generic.service.AnalysisLockService;
import org.rostilos.codecrow.pipelineagent.generic.service.PipelineJobService;
import org.rostilos.codecrow.pipelineagent.generic.service.RagIndexTrackingService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.function.Consumer;

/**
 * Service for RAG indexing operations that download from VCS.
 * Handles:
 * - Initial full repository indexing via VCS archive download
 * - Re-indexing operations
 * - Coordination with locks and status tracking
 */
@Service
public class VcsRagIndexingService {
    private static final Logger log = LoggerFactory.getLogger(VcsRagIndexingService.class);

    private final ProjectRepository projectRepository;
    private final VcsClientProvider vcsClientProvider;
    private final RagIndexingService ragIndexingService;
    private final RagIndexTrackingService ragIndexTrackingService;
    private final AnalysisLockService analysisLockService;
    private final PipelineJobService pipelineJobService;

    @Value("${codecrow.rag.api.enabled:false}")
    private boolean ragApiEnabled;

    public VcsRagIndexingService(
            ProjectRepository projectRepository,
            VcsClientProvider vcsClientProvider,
            RagIndexingService ragIndexingService,
            RagIndexTrackingService ragIndexTrackingService,
            AnalysisLockService analysisLockService,
            PipelineJobService pipelineJobService
    ) {
        this.projectRepository = projectRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.ragIndexingService = ragIndexingService;
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.analysisLockService = analysisLockService;
        this.pipelineJobService = pipelineJobService;
    }

    /**
     * Index a project from VCS.
     * Downloads the repository archive and indexes it in the RAG pipeline.
     *
     * @param authProject authenticated project info from JWT
     * @param requestBranch optional branch to index (overrides project config)
     * @param messageConsumer consumer for streaming progress messages
     * @return indexing result
     */
    public Map<String, Object> indexProjectFromVcs(
            ProjectDTO authProject,
            String requestBranch,
            Consumer<Map<String, Object>> messageConsumer
    ) {
        if (!ragApiEnabled) {
            log.warn("RAG API is disabled, skipping indexing");
            return Map.of("status", "skipped", "message", "RAG API is disabled");
        }

        if (!ragIndexingService.isAvailable()) {
            log.warn("RAG pipeline is not available");
            return Map.of("status", "error", "message", "RAG pipeline service is not available");
        }

        // Load full project with all VCS bindings - use fetch join to eagerly load VcsConnection
        // to avoid LazyInitializationException when accessing VcsConnection outside transaction
        Project project = projectRepository.findByIdWithFullDetails(authProject.id())
                .orElseThrow(() -> new NoSuchElementException("Project not found: " + authProject.id()));

        // Check RAG is enabled for project
        ProjectConfig config = project.getConfiguration();
        if (config == null || config.ragConfig() == null || !config.ragConfig().enabled()) {
            log.info("RAG is not enabled for project: {}", project.getName());
            return Map.of("status", "skipped", "message", "RAG is not enabled for this project");
        }

        VcsConnection vcsConnection;
        String workspaceSlug;
        String repoSlug;
        
        if (project.getVcsBinding() != null && project.getVcsBinding().getVcsConnection() != null) {
            vcsConnection = project.getVcsBinding().getVcsConnection();
            workspaceSlug = project.getVcsBinding().getWorkspace();
            repoSlug = project.getVcsBinding().getRepoSlug();
        } else if (project.getVcsRepoBinding() != null && project.getVcsRepoBinding().getVcsConnection() != null) {
            VcsRepoBinding repoBinding = project.getVcsRepoBinding();
            vcsConnection = repoBinding.getVcsConnection();
            workspaceSlug = repoBinding.getExternalNamespace();
            repoSlug = repoBinding.getExternalRepoSlug();
        } else {
            log.warn("Project {} has no VCS binding", project.getName());
            return Map.of("status", "error", "message", "Project has no VCS connection");
        }

        // Determine branch to index
        String branch = determineBranch(requestBranch, config);
        
        Job job = pipelineJobService.createRagInitialIndexJob(project, null);
        if (job != null) {
            pipelineJobService.getJobService().startJob(job);
            pipelineJobService.logToJob(job, JobLogLevel.INFO, "init", 
                    "Starting RAG indexing for branch: " + branch);
        }
        
        messageConsumer.accept(Map.of(
                "type", "progress",
                "stage", "init",
                "message", "Starting RAG indexing for branch: " + branch
        ));

        // Check if we can start indexing
        if (!ragIndexTrackingService.canStartIndexing(project)) {
            log.warn("RAG indexing already in progress for project: {}", project.getName());
            if (job != null) {
                pipelineJobService.failJob(job, "RAG indexing is already in progress");
            }
            return Map.of("status", "locked", "message", "RAG indexing is already in progress");
        }

        // Try to acquire lock
        Optional<String> lockKey = analysisLockService.acquireLock(
                project, branch, AnalysisLockType.RAG_INDEXING
        );

        if (lockKey.isEmpty()) {
            log.warn("Failed to acquire RAG indexing lock for project: {}", project.getName());
            if (job != null) {
                pipelineJobService.failJob(job, "Could not acquire lock for RAG indexing");
            }
            return Map.of("status", "locked", "message", "Could not acquire lock for RAG indexing");
        }

        try {
            return performIndexing(project, vcsConnection, workspaceSlug, repoSlug, branch, config, messageConsumer, job);
        } finally {
            analysisLockService.releaseLock(lockKey.get());
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
            Job job
    ) {
        try {
            // Get VCS client
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);

            // Get latest commit hash
            String vcsMsg = "Fetching latest commit info...";
            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "vcs",
                    "message", vcsMsg
            ));
            pipelineJobService.logToJob(job, JobLogLevel.INFO, "vcs", vcsMsg);

            String commitHash = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, branch);
            if (commitHash == null) {
                String errorMsg = "Failed to get latest commit for branch: " + branch;
                pipelineJobService.failJob(job, errorMsg);
                return Map.of("status", "error", "message", errorMsg);
            }

            // Mark indexing started
            ragIndexTrackingService.markIndexingStarted(project, branch, commitHash);

            // Download repository archive to temporary file (streaming to avoid OOM)
            String downloadMsg = "Downloading repository archive...";
            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "download",
                    "message", downloadMsg
            ));
            pipelineJobService.logToJob(job, JobLogLevel.INFO, "download", downloadMsg);

            Path tempArchiveFile = Files.createTempFile("codecrow-archive-", ".zip");
            try {
                long archiveSize = vcsClient.downloadRepositoryArchiveToFile(workspaceSlug, repoSlug, branch, tempArchiveFile);
                log.info("Downloaded archive: {} bytes for {}/{}", archiveSize, workspaceSlug, repoSlug);

                String downloadedMsg = "Downloaded " + formatBytes(archiveSize);
                messageConsumer.accept(Map.of(
                        "type", "progress",
                        "stage", "download",
                        "message", downloadedMsg
                ));
                pipelineJobService.logToJob(job, JobLogLevel.INFO, "download", downloadedMsg);

                // Index the repository
                String indexingMsg = "Indexing repository in RAG pipeline...";
                messageConsumer.accept(Map.of(
                        "type", "progress",
                        "stage", "indexing",
                        "message", indexingMsg
                ));
                pipelineJobService.logToJob(job, JobLogLevel.INFO, "indexing", indexingMsg);

                // Get exclude patterns from project config
                var excludePatterns = config.ragConfig() != null ? config.ragConfig().excludePatterns() : null;
                if (excludePatterns != null && !excludePatterns.isEmpty()) {
                    log.info("Using {} exclude patterns from project config", excludePatterns.size());
                    String excludeMsg = "Excluding " + excludePatterns.size() + " custom patterns";
                    messageConsumer.accept(Map.of(
                            "type", "progress",
                            "stage", "indexing",
                            "message", excludeMsg
                    ));
                    pipelineJobService.logToJob(job, JobLogLevel.INFO, "indexing", excludeMsg);
                }

                Map<String, Object> result = ragIndexingService.indexFromArchiveFile(
                        tempArchiveFile,
                        project.getWorkspace().getName(),
                        project.getNamespace(),
                        branch,
                        commitHash,
                        excludePatterns
                );

                // Mark indexing completed
                Integer filesIndexed = result.get("files_indexed") != null 
                        ? ((Number) result.get("files_indexed")).intValue() 
                        : null;
                ragIndexTrackingService.markIndexingCompleted(project, branch, commitHash, filesIndexed);

                String completeMsg = "RAG indexing completed successfully. Files indexed: " + (filesIndexed != null ? filesIndexed : 0);
                messageConsumer.accept(Map.of(
                        "type", "complete",
                        "stage", "done",
                        "message", completeMsg,
                        "filesIndexed", filesIndexed != null ? filesIndexed : 0
                ));
                pipelineJobService.logToJob(job, JobLogLevel.INFO, "complete", completeMsg);
                
                if (job != null) {
                    pipelineJobService.completeJob(job, null);
                }

                log.info("RAG indexing completed for project {} branch {}: {} files", 
                        project.getName(), branch, filesIndexed);

                return Map.of(
                        "status", "success",
                        "branch", branch,
                        "commitHash", commitHash,
                        "filesIndexed", filesIndexed != null ? filesIndexed : 0
                );
            } finally {
                // Clean up temporary archive file
                try {
                    Files.deleteIfExists(tempArchiveFile);
                } catch (IOException e) {
                    log.warn("Failed to delete temporary archive file: {}", tempArchiveFile, e);
                }
            }

        } catch (IOException e) {
            log.error("RAG indexing failed for project {}", project.getName(), e);
            ragIndexTrackingService.markIndexingFailed(project, e.getMessage());
            
            messageConsumer.accept(Map.of(
                    "type", "error",
                    "stage", "failed",
                    "message", "RAG indexing failed: " + e.getMessage()
            ));
            
            if (job != null) {
                pipelineJobService.logToJob(job, JobLogLevel.ERROR, "error", "RAG indexing failed: " + e.getMessage());
                pipelineJobService.failJob(job, "RAG indexing failed: " + e.getMessage());
            }
            
            return Map.of("status", "error", "message", e.getMessage());
        }
    }

    private String determineBranch(String requestBranch, ProjectConfig config) {
        // Priority: request > project RAG config > project default > "main"
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
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return String.format("%.1f KB", bytes / 1024.0);
        return String.format("%.1f MB", bytes / (1024.0 * 1024.0));
    }

    /**
     * Check if RAG indexing should be automatically triggered for a project.
     * Called after project setup/connection changes.
     */
    @Transactional(readOnly = true)
    public boolean shouldAutoIndex(Project project) {
        if (!ragApiEnabled) {
            return false;
        }

        ProjectConfig config = project.getConfiguration();
        if (config == null || config.ragConfig() == null || !config.ragConfig().enabled()) {
            return false;
        }

        // Check if already indexed
        Optional<RagIndexStatus> status = ragIndexTrackingService.getIndexStatus(project);
        if (status.isPresent()) {
            // Already indexed or in progress
            return false;
        }

        // Has VCS binding
        return project.getVcsBinding() != null && project.getVcsBinding().getVcsConnection() != null;
    }
}
