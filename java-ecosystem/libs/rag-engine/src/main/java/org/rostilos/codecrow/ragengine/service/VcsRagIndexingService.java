package org.rostilos.codecrow.ragengine.service;

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
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.service.AnalysisJobService;
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

@Service
public class VcsRagIndexingService {
    private static final Logger log = LoggerFactory.getLogger(VcsRagIndexingService.class);

    private final ProjectRepository projectRepository;
    private final VcsClientProvider vcsClientProvider;
    private final RagIndexingService ragIndexingService;
    private final RagIndexTrackingService ragIndexTrackingService;
    private final AnalysisLockService analysisLockService;
    private final AnalysisJobService jobService;

    @Value("${codecrow.rag.api.enabled:false}")
    private boolean ragApiEnabled;

    public VcsRagIndexingService(
            ProjectRepository projectRepository,
            VcsClientProvider vcsClientProvider,
            RagIndexingService ragIndexingService,
            RagIndexTrackingService ragIndexTrackingService,
            AnalysisLockService analysisLockService,
            AnalysisJobService jobService
    ) {
        this.projectRepository = projectRepository;
        this.vcsClientProvider = vcsClientProvider;
        this.ragIndexingService = ragIndexingService;
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.analysisLockService = analysisLockService;
        this.jobService = jobService;
    }

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

        String branch = determineBranch(requestBranch, config);
        
        Job job = jobService.createRagIndexJob(project, null);
        if (job != null) {
            jobService.startJob(job);
            jobService.logToJob(job, JobLogLevel.INFO, "init", 
                    "Starting RAG indexing for branch: " + branch);
        }
        
        messageConsumer.accept(Map.of(
                "type", "progress",
                "stage", "init",
                "message", "Starting RAG indexing for branch: " + branch
        ));

        if (!ragIndexTrackingService.canStartIndexing(project)) {
            log.warn("RAG indexing already in progress for project: {}", project.getName());
            if (job != null) {
                jobService.failJob(job, "RAG indexing is already in progress");
            }
            return Map.of("status", "locked", "message", "RAG indexing is already in progress");
        }

        Optional<String> lockKey = analysisLockService.acquireLock(
                project, branch, AnalysisLockType.RAG_INDEXING
        );

        if (lockKey.isEmpty()) {
            log.warn("Failed to acquire RAG indexing lock for project: {}", project.getName());
            if (job != null) {
                jobService.failJob(job, "Could not acquire lock for RAG indexing");
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
            VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);

            String vcsMsg = "Fetching latest commit info...";
            messageConsumer.accept(Map.of(
                    "type", "progress",
                    "stage", "vcs",
                    "message", vcsMsg
            ));
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
                    "message", downloadMsg
            ));
            jobService.logToJob(job, JobLogLevel.INFO, "download", downloadMsg);

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
                jobService.logToJob(job, JobLogLevel.INFO, "download", downloadedMsg);

                String indexingMsg = "Indexing repository in RAG pipeline...";
                messageConsumer.accept(Map.of(
                        "type", "progress",
                        "stage", "indexing",
                        "message", indexingMsg
                ));
                jobService.logToJob(job, JobLogLevel.INFO, "indexing", indexingMsg);

                var excludePatterns = config.ragConfig() != null ? config.ragConfig().excludePatterns() : null;
                if (excludePatterns != null && !excludePatterns.isEmpty()) {
                    log.info("Using {} exclude patterns from project config", excludePatterns.size());
                    String excludeMsg = "Excluding " + excludePatterns.size() + " custom patterns";
                    messageConsumer.accept(Map.of(
                            "type", "progress",
                            "stage", "indexing",
                            "message", excludeMsg
                    ));
                    jobService.logToJob(job, JobLogLevel.INFO, "indexing", excludeMsg);
                }

                Map<String, Object> result = ragIndexingService.indexFromArchiveFile(
                        tempArchiveFile,
                        project.getWorkspace().getName(),
                        project.getNamespace(),
                        branch,
                        commitHash,
                        excludePatterns
                );

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
                jobService.logToJob(job, JobLogLevel.INFO, "complete", completeMsg);
                
                if (job != null) {
                    jobService.completeJob(job, null);
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
                jobService.logToJob(job, JobLogLevel.ERROR, "error", "RAG indexing failed: " + e.getMessage());
                jobService.failJob(job, "RAG indexing failed: " + e.getMessage());
            }
            
            return Map.of("status", "error", "message", e.getMessage());
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
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return String.format("%.1f KB", bytes / 1024.0);
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

        return project.getVcsBinding() != null && project.getVcsBinding().getVcsConnection() != null;
    }
}
