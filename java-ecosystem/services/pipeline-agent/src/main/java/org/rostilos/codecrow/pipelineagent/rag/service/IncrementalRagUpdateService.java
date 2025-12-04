package org.rostilos.codecrow.pipelineagent.rag.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.pipelineagent.generic.service.RagIndexTrackingService;
import org.rostilos.codecrow.pipelineagent.rag.client.RagPipelineClient;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.concurrent.*;

/**
 * Service for incremental RAG updates.
 * Fetches individual file contents from VCS and updates the RAG index.
 */
@Service
public class IncrementalRagUpdateService {
    private static final Logger log = LoggerFactory.getLogger(IncrementalRagUpdateService.class);

    private final VcsClientProvider vcsClientProvider;
    private final RagPipelineClient ragPipelineClient;
    private final RagIndexTrackingService ragIndexTrackingService;
    
    @Value("${codecrow.rag.api.enabled:false}")
    private boolean ragApiEnabled;
    
    @Value("${codecrow.rag.parallel.requests:10}")
    private int parallelRequests;

    public IncrementalRagUpdateService(
            VcsClientProvider vcsClientProvider,
            RagPipelineClient ragPipelineClient,
            RagIndexTrackingService ragIndexTrackingService
    ) {
        this.vcsClientProvider = vcsClientProvider;
        this.ragPipelineClient = ragPipelineClient;
        this.ragIndexTrackingService = ragIndexTrackingService;
    }

    /**
     * Check if incremental RAG update should be performed.
     */
    public boolean shouldPerformIncrementalUpdate(Project project) {
        if (!ragApiEnabled) {
            return false;
        }

        ProjectConfig config = project.getConfiguration();
        if (config == null || config.ragConfig() == null || !config.ragConfig().enabled()) {
            return false;
        }

        return ragIndexTrackingService.isProjectIndexed(project);
    }

    /**
     * Perform incremental RAG update for changed files.
     * 
     * @param project the project
     * @param vcsConnection VCS connection
     * @param workspaceSlug VCS workspace
     * @param repoSlug VCS repository
     * @param branch branch name
     * @param commitHash commit hash
     * @param addedOrModifiedFiles files that were added or modified
     * @param deletedFiles files that were deleted
     * @return update result
     */
    public Map<String, Object> performIncrementalUpdate(
            Project project,
            VcsConnection vcsConnection,
            String workspaceSlug,
            String repoSlug,
            String branch,
            String commitHash,
            Set<String> addedOrModifiedFiles,
            Set<String> deletedFiles
    ) throws IOException {
        log.info("Starting incremental RAG update for project {} branch {}: {} files to update, {} to delete",
                project.getName(), branch, addedOrModifiedFiles.size(), deletedFiles.size());

        String projectWorkspace = project.getWorkspace().getName();
        String projectNamespace = project.getNamespace();

        Map<String, Object> result = new HashMap<>();
        result.put("branch", branch);
        result.put("commitHash", commitHash);

        // Handle deleted files first
        if (!deletedFiles.isEmpty()) {
            try {
                Map<String, Object> deleteResult = ragPipelineClient.deleteFiles(
                        new ArrayList<>(deletedFiles),
                        projectWorkspace,
                        projectNamespace,
                        branch
                );
                result.put("deletedFiles", deletedFiles.size());
                log.info("Deleted {} files from RAG index", deletedFiles.size());
            } catch (Exception e) {
                log.error("Failed to delete files from RAG index", e);
                result.put("deleteError", e.getMessage());
            }
        }

        // Handle added/modified files
        if (!addedOrModifiedFiles.isEmpty()) {
            try {
                // Fetch file contents and write to temp directory
                Path tempDir = Files.createTempDirectory("codecrow-rag-incremental-");
                try {
                    int fetchedFiles = fetchFilesToTempDir(
                            vcsConnection,
                            workspaceSlug,
                            repoSlug,
                            branch,
                            addedOrModifiedFiles,
                            tempDir
                    );

                    // Update RAG index with fetched files
                    Map<String, Object> updateResult = ragPipelineClient.updateFiles(
                            new ArrayList<>(addedOrModifiedFiles),
                            tempDir.toString(),
                            projectWorkspace,
                            projectNamespace,
                            branch,
                            commitHash
                    );
                    
                    result.put("updatedFiles", fetchedFiles);
                    result.putAll(updateResult);
                    log.info("Updated {} files in RAG index", fetchedFiles);
                    
                } finally {
                    // Clean up temp directory
                    deleteDirectory(tempDir.toFile());
                }
            } catch (Exception e) {
                log.error("Failed to update files in RAG index", e);
                result.put("updateError", e.getMessage());
            }
        }

        result.put("status", "completed");
        return result;
    }

    /**
     * Fetch files from VCS and save to temp directory.
     * Uses parallel requests for efficiency.
     */
    private int fetchFilesToTempDir(
            VcsConnection vcsConnection,
            String workspaceSlug,
            String repoSlug,
            String branch,
            Set<String> filePaths,
            Path tempDir
    ) throws IOException {
        VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);
        
        ExecutorService executor = Executors.newFixedThreadPool(Math.min(parallelRequests, filePaths.size()));
        List<CompletableFuture<Boolean>> futures = new ArrayList<>();

        for (String filePath : filePaths) {
            CompletableFuture<Boolean> future = CompletableFuture.supplyAsync(() -> {
                try {
                    String content = vcsClient.getFileContent(workspaceSlug, repoSlug, filePath, branch);
                    if (content != null) {
                        Path targetPath = tempDir.resolve(filePath);
                        Files.createDirectories(targetPath.getParent());
                        Files.writeString(targetPath, content);
                        return true;
                    }
                    return false;
                } catch (IOException e) {
                    log.warn("Failed to fetch file {}: {}", filePath, e.getMessage());
                    return false;
                }
            }, executor);
            futures.add(future);
        }

        // Wait for all futures to complete
        int successCount = 0;
        for (CompletableFuture<Boolean> future : futures) {
            try {
                if (future.get(30, TimeUnit.SECONDS)) {
                    successCount++;
                }
            } catch (Exception e) {
                log.warn("File fetch task failed: {}", e.getMessage());
            }
        }

        executor.shutdown();
        try {
            executor.awaitTermination(60, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }

        return successCount;
    }

    /**
     * Parse diff to categorize files into added/modified and deleted.
     */
    public DiffResult parseDiffForRag(String rawDiff) {
        Set<String> addedOrModified = new HashSet<>();
        Set<String> deleted = new HashSet<>();

        if (rawDiff == null || rawDiff.isBlank()) {
            return new DiffResult(addedOrModified, deleted);
        }

        String[] lines = rawDiff.split("\\r?\\n");
        String currentFile = null;
        boolean isDelete = false;

        for (String line : lines) {
            if (line.startsWith("diff --git")) {
                // Parse: diff --git a/path b/path
                String[] parts = line.split("\\s+");
                if (parts.length >= 4) {
                    String bPath = parts[3];
                    if (bPath.startsWith("b/")) {
                        currentFile = bPath.substring(2);
                    }
                }
                isDelete = false;
            } else if (line.startsWith("deleted file mode")) {
                isDelete = true;
            } else if (line.startsWith("new file mode") || line.startsWith("index ")) {
                // File is being added or modified
                if (currentFile != null && !isDelete) {
                    addedOrModified.add(currentFile);
                } else if (currentFile != null && isDelete) {
                    deleted.add(currentFile);
                }
            }
        }

        // Handle files that weren't explicitly categorized (treat as modified)
        // This is a simplified approach - full diff parsing would be more complex
        
        return new DiffResult(addedOrModified, deleted);
    }

    public record DiffResult(
            Set<String> addedOrModified,
            Set<String> deleted
    ) {}

    private void deleteDirectory(java.io.File dir) {
        if (dir.exists()) {
            java.io.File[] files = dir.listFiles();
            if (files != null) {
                for (java.io.File file : files) {
                    if (file.isDirectory()) {
                        deleteDirectory(file);
                    } else {
                        file.delete();
                    }
                }
            }
            dir.delete();
        }
    }
}
