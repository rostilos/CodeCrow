package org.rostilos.codecrow.pipelineagent.rag.service;

import org.rostilos.codecrow.core.dto.project.ProjectDTO;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.pipelineagent.generic.service.AnalysisLockService;
import org.rostilos.codecrow.pipelineagent.generic.service.RagIndexTrackingService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
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

    @Value("${codecrow.rag.api.enabled:false}")
    private boolean ragApiEnabled;

    public VcsRagIndexingService(
            ProjectRepository projectRepository,
            VcsClientProvider vcsClientProvider,
            RagIndexingService ragIndexingService,
            RagIndexTrackingService ragIndexTrackingService,
            AnalysisLockService analysisLockService
    ) {
        this.projectRepository = projectRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.ragIndexingService = ragIndexingService;
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.analysisLockService = analysisLockService;
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
        
        messageConsumer.accept(Map.of(
                "type", "progress",
                "stage", "init",
                "message", "Starting RAG indexing for branch: " + branch
        ));

        // Check if we can start indexing
        if (!ragIndexTrackingService.canStartIndexing(project)) {
            log.warn("RAG indexing already in progress for project: {}", project.getName());
            return Map.of("status", "locked", "message", "RAG indexing is already in progress");
        }

        // Try to acquire lock
        Optional<String> lockKey = analysisLockService.acquireLock(
                project, branch, AnalysisLockType.RAG_INDEXING
        );

        if (lockKey.isEmpty()) {
            log.warn("Failed to acquire RAG indexing lock for project: {}", project.getName());
            return Map.of("status", "locked", "message", "Could not acquire lock for RAG indexing");
        }

        try {
            return performIndexing(project, vcsConnection, workspaceSlug, repoSlug, branch, config, messageConsumer);
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
            Consumer<Map<String, Object>> messageConsumer
    ) {
        try {
            // Get VCS client
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);

            // Get latest commit hash
            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "vcs",
                    "message", "Fetching latest commit info..."
            ));

            String commitHash = vcsClient.getLatestCommitHash(workspaceSlug, repoSlug, branch);
            if (commitHash == null) {
                return Map.of("status", "error", "message", "Failed to get latest commit for branch: " + branch);
            }

            // Mark indexing started
            ragIndexTrackingService.markIndexingStarted(project, branch, commitHash);

            // Download repository archive
            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "download",
                    "message", "Downloading repository archive..."
            ));

            byte[] archiveData = vcsClient.downloadRepositoryArchive(workspaceSlug, repoSlug, branch);
            log.info("Downloaded archive: {} bytes for {}/{}", archiveData.length, workspaceSlug, repoSlug);

            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "download",
                    "message", "Downloaded " + formatBytes(archiveData.length)
            ));

            // Index the repository
            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "indexing",
                    "message", "Indexing repository in RAG pipeline..."
            ));

            // Get exclude patterns from project config
            var excludePatterns = config.ragConfig() != null ? config.ragConfig().excludePatterns() : null;
            if (excludePatterns != null && !excludePatterns.isEmpty()) {
                log.info("Using {} exclude patterns from project config", excludePatterns.size());
                messageConsumer.accept(Map.of(
                        "type", "progress",
                        "stage", "indexing",
                        "message", "Excluding " + excludePatterns.size() + " custom patterns"
                ));
            }

            Map<String, Object> result = ragIndexingService.indexFromArchive(
                    archiveData,
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

            messageConsumer.accept(Map.of(
                    "type", "complete",
                    "stage", "done",
                    "message", "RAG indexing completed successfully",
                    "filesIndexed", filesIndexed != null ? filesIndexed : 0
            ));

            log.info("RAG indexing completed for project {} branch {}: {} files", 
                    project.getName(), branch, filesIndexed);

            return Map.of(
                    "status", "success",
                    "branch", branch,
                    "commitHash", commitHash,
                    "filesIndexed", filesIndexed != null ? filesIndexed : 0
            );

        } catch (IOException e) {
            log.error("RAG indexing failed for project {}", project.getName(), e);
            ragIndexTrackingService.markIndexingFailed(project, e.getMessage());
            
            messageConsumer.accept(Map.of(
                    "type", "error",
                    "stage", "failed",
                    "message", "RAG indexing failed: " + e.getMessage()
            ));
            
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
