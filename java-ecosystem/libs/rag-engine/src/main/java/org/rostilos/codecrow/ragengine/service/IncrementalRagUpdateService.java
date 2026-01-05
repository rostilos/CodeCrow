package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
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

        if (!addedOrModifiedFiles.isEmpty()) {
            try {
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

    public DiffResult parseDiffForRag(String rawDiff) {
        Set<String> addedOrModified = new HashSet<>();
        Set<String> deleted = new HashSet<>();

        if (rawDiff == null || rawDiff.isBlank()) {
            return new DiffResult(addedOrModified, deleted);
        }

        String[] lines = rawDiff.split("\\r?\\n");
        String currentFile = null;
        boolean isDelete = false;
        boolean fileProcessed = false;

        for (String line : lines) {
            if (line.startsWith("diff --git")) {
                // Process previous file if we haven't categorized it yet
                if (currentFile != null && !fileProcessed) {
                    // Default to modified if we haven't seen explicit delete marker
                    if (!isDelete) {
                        addedOrModified.add(currentFile);
                    }
                }
                
                // Parse new file
                String[] parts = line.split("\\s+");
                if (parts.length >= 4) {
                    String bPath = parts[3];
                    if (bPath.startsWith("b/")) {
                        currentFile = bPath.substring(2);
                    }
                }
                isDelete = false;
                fileProcessed = false;
            } else if (line.startsWith("deleted file mode")) {
                isDelete = true;
                if (currentFile != null) {
                    deleted.add(currentFile);
                    fileProcessed = true;
                }
            } else if (line.startsWith("new file mode")) {
                if (currentFile != null && !isDelete) {
                    addedOrModified.add(currentFile);
                    fileProcessed = true;
                }
            }
        }
        
        // Don't forget to process the last file
        if (currentFile != null && !fileProcessed) {
            if (!isDelete) {
                addedOrModified.add(currentFile);
            }
        }
        
        log.info("Parsed diff: {} added/modified files, {} deleted files", 
                addedOrModified.size(), deleted.size());
        
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
