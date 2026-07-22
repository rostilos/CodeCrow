package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
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
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.*;
import java.util.concurrent.*;

@Service
public class IncrementalRagUpdateService {
    private static final Logger log = LoggerFactory.getLogger(IncrementalRagUpdateService.class);

    private final VcsClientProvider vcsClientProvider;
    private final RagPipelineClient ragPipelineClient;
    private final RagIndexTrackingService ragIndexTrackingService;
    private final BranchArchiveService branchArchiveService;

    @Value("${codecrow.rag.api.enabled:true}")
    private boolean ragApiEnabled;

    @Value("${codecrow.rag.parallel.requests:10}")
    private int parallelRequests;

    @Value("${codecrow.rag.incremental.update-batch-size:25}")
    private int updateBatchSize;

    @Value("${codecrow.rag.incremental.archive-file-threshold:25}")
    private int archiveFileThreshold;

    @Value("${codecrow.rag.incremental.max-attempts:3}")
    private int ragApiMaxAttempts;

    @Value("${codecrow.rag.incremental.retry-delay-ms:1000}")
    private long ragApiRetryDelayMs;

    public IncrementalRagUpdateService(
            VcsClientProvider vcsClientProvider,
            RagPipelineClient ragPipelineClient,
            RagIndexTrackingService ragIndexTrackingService,
            BranchArchiveService branchArchiveService) {
        this.vcsClientProvider = vcsClientProvider;
        this.ragPipelineClient = ragPipelineClient;
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.branchArchiveService = branchArchiveService;
    }

    public boolean shouldPerformIncrementalUpdate(Project project) {
        if (!ragApiEnabled) {
            log.info("shouldPerformIncrementalUpdate: ragApiEnabled=false for project={}", project.getId());
            return false;
        }

        ProjectConfig config = project.getConfiguration();
        if (config == null) {
            log.info("shouldPerformIncrementalUpdate: config is null for project={}", project.getId());
            return false;
        }

        if (config.ragConfig() == null) {
            log.info("shouldPerformIncrementalUpdate: ragConfig is null for project={}", project.getId());
            return false;
        }

        if (!config.ragConfig().enabled()) {
            log.info("shouldPerformIncrementalUpdate: ragConfig.enabled=false for project={}", project.getId());
            return false;
        }

        boolean isIndexed = ragIndexTrackingService.isProjectIndexed(project);
        log.info("shouldPerformIncrementalUpdate: project={} isProjectIndexed={}", project.getId(), isIndexed);
        return isIndexed;
    }

    public Map<String, Object> performIncrementalUpdate(
            Project project,
            VcsConnection vcsConnection,
            String workspaceSlug,
            String repoSlug,
            String branch,
            String commitHash,
            Set<String> addedFiles,
            Set<String> modifiedFiles,
            Set<String> deletedFiles) throws IOException {
        int addedOrModifiedSize = addedFiles.size() + modifiedFiles.size();
        log.info(
                "Starting incremental RAG update for project {} branch {}: {} files to update ({} added), {} to delete",
                project.getName(), branch, addedOrModifiedSize, addedFiles.size(), deletedFiles.size());

        String projectWorkspace = project.getWorkspace().getName();
        String projectNamespace = project.getNamespace();

        Map<String, Object> result = new HashMap<>();
        result.put("branch", branch);
        result.put("commitHash", commitHash);

        if (!deletedFiles.isEmpty()) {
            executeDeleteBatches(
                    sortedList(deletedFiles),
                    projectWorkspace,
                    projectNamespace,
                    branch);
            result.put("deletedFiles", deletedFiles.size());
            log.info("Deleted {} files from RAG index", deletedFiles.size());
        }

        if (!addedFiles.isEmpty() || !modifiedFiles.isEmpty()) {
            Set<String> addedOrModifiedFiles = new LinkedHashSet<>();
            addedOrModifiedFiles.addAll(addedFiles);
            addedOrModifiedFiles.addAll(modifiedFiles);
            List<String> orderedAddedOrModifiedFiles = sortedList(addedOrModifiedFiles);

            Path tempDir = Files.createTempDirectory("codecrow-rag-incremental-",
                    PosixFilePermissions.asFileAttribute(
                            PosixFilePermissions.fromString("rwxrwxrwx")));
            try {
                String revision = commitHash != null && !commitHash.isBlank() ? commitHash : branch;
                int effectiveArchiveFileThreshold = Math.max(0, archiveFileThreshold);
                boolean useArchive = orderedAddedOrModifiedFiles.size() > effectiveArchiveFileThreshold;
                Set<String> fetchedFilePaths;
                if (useArchive) {
                    log.info("Using one repository archive at revision {} for {} incremental RAG files "
                                    + "(threshold: {})",
                            revision, orderedAddedOrModifiedFiles.size(), effectiveArchiveFileThreshold);
                    fetchedFilePaths = branchArchiveService.downloadAndExtractFilesToDirectory(
                            vcsConnection,
                            workspaceSlug,
                            repoSlug,
                            revision,
                            new LinkedHashSet<>(orderedAddedOrModifiedFiles),
                            tempDir);
                } else {
                    log.info("Using per-file VCS retrieval for {} incremental RAG files (threshold: {})",
                            orderedAddedOrModifiedFiles.size(), effectiveArchiveFileThreshold);
                    fetchedFilePaths = fetchFilesToTempDir(
                            vcsConnection,
                            workspaceSlug,
                            repoSlug,
                            revision,
                            orderedAddedOrModifiedFiles,
                            tempDir);
                }
                List<String> fetchedFiles = orderedAddedOrModifiedFiles.stream()
                        .filter(fetchedFilePaths::contains)
                        .toList();

                Map<String, Object> updateResult = fetchedFiles.isEmpty()
                        ? Map.of()
                        : executeUpdateBatches(
                                fetchedFiles,
                                projectWorkspace,
                                projectNamespace,
                                branch,
                                commitHash,
                                tempDir.toString());

                result.put("updatedFiles", fetchedFiles.size());
                long fetchedAddedFiles = fetchedFiles.stream().filter(addedFiles::contains).count();
                result.put("addedFilesCount", Math.toIntExact(fetchedAddedFiles));
                result.put("fileFetchMode", useArchive ? "archive" : "per-file");
                result.putAll(updateResult);
                log.info("Updated {} files in RAG index", fetchedFiles.size());

            } finally {
                deleteDirectory(tempDir.toFile());
            }
        }

        result.put("status", "completed");
        return result;
    }

    private void executeDeleteBatches(
            List<String> deletedFiles,
            String projectWorkspace,
            String projectNamespace,
            String branch) throws IOException {
        for (List<String> batch : partition(deletedFiles)) {
            executeWithRetry("delete RAG files", () -> ragPipelineClient.deleteFiles(
                    batch,
                    projectWorkspace,
                    projectNamespace,
                    branch));
        }
    }

    private Map<String, Object> executeUpdateBatches(
            List<String> updatedFiles,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commitHash,
            String tempDir) throws IOException {
        Map<String, Object> lastResult = Map.of();
        for (List<String> batch : partition(updatedFiles)) {
            lastResult = executeWithRetry("update RAG files", () -> ragPipelineClient.updateFiles(
                        batch,
                        tempDir,
                        projectWorkspace,
                        projectNamespace,
                        branch,
                        commitHash));
        }
        return lastResult;
    }

    @FunctionalInterface
    private interface RagApiCall {
        Map<String, Object> execute() throws IOException;
    }

    private Map<String, Object> executeWithRetry(String operation, RagApiCall call) throws IOException {
        IOException lastFailure = null;
        int maxAttempts = Math.max(1, ragApiMaxAttempts);
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
                return call.execute();
            } catch (IOException e) {
                lastFailure = e;
                if (attempt >= maxAttempts || !isRetryableRagFailure(e)) {
                    throw e;
                }
                log.warn("{} failed on attempt {}/{}: {}. Retrying...",
                        operation, attempt, maxAttempts, e.getMessage());
                sleepBeforeRetry();
            }
        }
        throw lastFailure != null ? lastFailure : new IOException(operation + " failed");
    }

    private boolean isRetryableRagFailure(IOException e) {
        String message = e.getMessage();
        if (message == null) {
            return true;
        }
        String normalized = message.toLowerCase(Locale.ROOT);
        return normalized.contains("timed out")
                || normalized.contains("timeout")
                || normalized.contains("connection")
                || normalized.contains("temporarily")
                || normalized.contains("503")
                || normalized.contains("502")
                || normalized.contains("504")
                || normalized.contains("500");
    }

    private void sleepBeforeRetry() throws IOException {
        if (ragApiRetryDelayMs <= 0) {
            return;
        }
        try {
            Thread.sleep(ragApiRetryDelayMs);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("Interrupted while waiting to retry RAG API call", e);
        }
    }

    private List<List<String>> partition(List<String> filePaths) {
        if (filePaths.isEmpty()) {
            return List.of();
        }
        int batchSize = updateBatchSize > 0 ? updateBatchSize : filePaths.size();
        List<List<String>> batches = new ArrayList<>();
        for (int i = 0; i < filePaths.size(); i += batchSize) {
            batches.add(filePaths.subList(i, Math.min(i + batchSize, filePaths.size())));
        }
        return batches;
    }

    private List<String> sortedList(Collection<String> values) {
        return values.stream()
                .filter(Objects::nonNull)
                .filter(value -> !value.isBlank())
                .sorted()
                .toList();
    }

    private Set<String> fetchFilesToTempDir(
            VcsConnection vcsConnection,
            String workspaceSlug,
            String repoSlug,
            String branchOrCommit,
            List<String> filePaths,
            Path tempDir) throws IOException {
        VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);

        int workerCount = Math.min(Math.max(1, parallelRequests), filePaths.size());
        ExecutorService executor = Executors.newFixedThreadPool(workerCount);
        try {
            List<CompletableFuture<Boolean>> futures = new ArrayList<>();

            for (String filePath : filePaths) {
                CompletableFuture<Boolean> future = CompletableFuture.supplyAsync(() -> {
                    try {
                        String content = vcsClient.getFileContent(
                                workspaceSlug, repoSlug, filePath, branchOrCommit);
                        if (content != null) {
                            Path targetPath = resolveTargetPath(tempDir, filePath);
                            if (targetPath == null) {
                                log.warn("Skipping file path outside incremental RAG temp directory: {}", filePath);
                                return false;
                            }
                            Path parentDir = targetPath.getParent();
                            Files.createDirectories(parentDir);
                            // Ensure all intermediate dirs are world-readable
                            // (shared /tmp volume between containers)
                            for (Path dir = parentDir; dir != null && dir.startsWith(tempDir); dir = dir.getParent()) {
                                dir.toFile().setReadable(true, false);
                                dir.toFile().setExecutable(true, false);
                            }
                            Files.writeString(targetPath, content);
                            targetPath.toFile().setReadable(true, false);
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

            Set<String> fetchedFiles = new LinkedHashSet<>();
            for (int i = 0; i < futures.size(); i++) {
                try {
                    if (futures.get(i).get(30, TimeUnit.SECONDS)) {
                        fetchedFiles.add(filePaths.get(i));
                    }
                } catch (Exception e) {
                    log.warn("File fetch task failed: {}", e.getMessage());
                }
            }

            return fetchedFiles;
        } finally {
            executor.shutdownNow();
            try {
                if (!executor.awaitTermination(10, TimeUnit.SECONDS)) {
                    log.warn("Some file fetch threads did not terminate within timeout");
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                log.warn("Interrupted while awaiting executor termination");
            }
        }
    }

    private Path resolveTargetPath(Path tempDir, String filePath) {
        try {
            Path normalizedTempDir = tempDir.toAbsolutePath().normalize();
            Path targetPath = normalizedTempDir.resolve(filePath).normalize();
            return targetPath.startsWith(normalizedTempDir) && !targetPath.equals(normalizedTempDir)
                    ? targetPath
                    : null;
        } catch (RuntimeException e) {
            return null;
        }
    }

    public DiffResult parseDiffForRag(String rawDiff) {
        Set<String> added = new HashSet<>();
        Set<String> modified = new HashSet<>();
        Set<String> deleted = new HashSet<>();

        if (rawDiff == null || rawDiff.isBlank()) {
            return new DiffResult(added, modified, deleted);
        }

        String[] lines = rawDiff.split("\\r?\\n");
        String currentFile = null;
        boolean isDelete = false;
        boolean fileProcessed = false;
        // Track rename operations: old path -> delete, new path -> add
        String renameFrom = null;

        for (String line : lines) {
            if (line.startsWith("diff --git")) {
                // Process previous file if we haven't categorized it yet
                if (currentFile != null && !fileProcessed) {
                    if (!isDelete) {
                        modified.add(currentFile);
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
                renameFrom = null;
            } else if (line.startsWith("deleted file mode")) {
                isDelete = true;
                if (currentFile != null) {
                    deleted.add(currentFile);
                    fileProcessed = true;
                }
            } else if (line.startsWith("new file mode")) {
                if (currentFile != null && !isDelete) {
                    added.add(currentFile);
                    fileProcessed = true;
                }
            } else if (line.startsWith("rename from ") || line.startsWith("copy from ")) {
                // Git rename/copy: "rename from old/path.java"
                // The old path should be deleted from the index
                renameFrom = line.substring(line.indexOf(' ', line.indexOf(' ') + 1) + 1).trim();
            } else if (line.startsWith("rename to ") || line.startsWith("copy to ")) {
                // Git rename/copy: "rename to new/path.java"
                // The new path should be added/indexed
                String renameTo = line.substring(line.indexOf(' ', line.indexOf(' ') + 1) + 1).trim();
                if (renameFrom != null && !renameFrom.isEmpty()) {
                    deleted.add(renameFrom);
                }
                if (!renameTo.isEmpty()) {
                    added.add(renameTo);
                }
                fileProcessed = true;
                renameFrom = null;
            }
        }

        // Don't forget to process the last file
        if (currentFile != null && !fileProcessed) {
            if (!isDelete) {
                modified.add(currentFile);
            }
        }

        log.info("Parsed diff: {} added, {} modified, {} deleted files",
                added.size(), modified.size(), deleted.size());

        return new DiffResult(added, modified, deleted);
    }

    public record DiffResult(
            Set<String> added,
            Set<String> modified,
            Set<String> deleted) {
    }

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
