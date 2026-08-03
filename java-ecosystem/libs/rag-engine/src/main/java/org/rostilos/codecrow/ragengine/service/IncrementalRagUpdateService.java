package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.analysisengine.service.BranchArchiveService;
import org.rostilos.codecrow.analysisengine.service.VcsFileRetrievalPolicy;
import org.rostilos.codecrow.analysisengine.util.TextFileEligibility;
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
import java.nio.file.attribute.PosixFilePermissions;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Collectors;

@Service
public class IncrementalRagUpdateService {
    private static final Logger log = LoggerFactory.getLogger(IncrementalRagUpdateService.class);

    private final VcsClientProvider vcsClientProvider;
    private final RagPipelineClient ragPipelineClient;
    private final RagIndexTrackingService ragIndexTrackingService;
    private final BranchArchiveService branchArchiveService;
    private final VcsFileRetrievalPolicy fileRetrievalPolicy;

    @Value("${codecrow.rag.api.enabled:true}")
    private boolean ragApiEnabled;

    @Value("${codecrow.rag.parallel.requests:10}")
    private int parallelRequests;

    @Value("${codecrow.rag.incremental.max-attempts:3}")
    private int ragApiMaxAttempts;

    @Value("${codecrow.rag.incremental.retry-delay-ms:1000}")
    private long ragApiRetryDelayMs;

    public IncrementalRagUpdateService(
            VcsClientProvider vcsClientProvider,
            RagPipelineClient ragPipelineClient,
            RagIndexTrackingService ragIndexTrackingService,
            BranchArchiveService branchArchiveService,
            VcsFileRetrievalPolicy fileRetrievalPolicy) {
        this.vcsClientProvider = vcsClientProvider;
        this.ragPipelineClient = ragPipelineClient;
        this.ragIndexTrackingService = ragIndexTrackingService;
        this.branchArchiveService = branchArchiveService;
        this.fileRetrievalPolicy = fileRetrievalPolicy;
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
        Set<String> skippedNonTextFiles = new LinkedHashSet<>();
        Set<String> indexableAddedFiles = filterTextCandidates(addedFiles, skippedNonTextFiles);
        Set<String> indexableModifiedFiles = filterTextCandidates(modifiedFiles, skippedNonTextFiles);
        int addedOrModifiedSize = indexableAddedFiles.size() + indexableModifiedFiles.size();
        log.info(
                "Starting incremental RAG update for project {} branch {}: {} text files to update "
                        + "({} added), {} to delete, {} non-text files skipped",
                project.getName(), branch, addedOrModifiedSize, indexableAddedFiles.size(),
                deletedFiles.size(), skippedNonTextFiles.size());

        String projectWorkspace = project.getWorkspace().getName();
        String projectNamespace = project.getNamespace();

        Map<String, Object> result = new HashMap<>();
        result.put("branch", branch);
        result.put("commitHash", commitHash);
        result.put("skippedFiles", skippedNonTextFiles.size());

        Set<String> addedOrModifiedFiles = new LinkedHashSet<>();
        addedOrModifiedFiles.addAll(indexableAddedFiles);
        addedOrModifiedFiles.addAll(indexableModifiedFiles);
        List<String> orderedAddedOrModifiedFiles =
                new ArrayList<>(sortedList(addedOrModifiedFiles));
        List<String> orderedDeletedFiles = sortedList(deletedFiles);
        if (orderedAddedOrModifiedFiles.isEmpty() && orderedDeletedFiles.isEmpty()) {
            result.put("status", "completed");
            return result;
        }

        Path tempDir = null;
        try {
            String revision = commitHash != null && !commitHash.isBlank() ? commitHash : branch;
            String repoBase = null;
            if (!orderedAddedOrModifiedFiles.isEmpty()) {
                tempDir = Files.createTempDirectory("codecrow-rag-incremental-",
                    PosixFilePermissions.asFileAttribute(
                            PosixFilePermissions.fromString("rwxrwxrwx")));
                int effectiveArchiveFileThreshold = fileRetrievalPolicy.archiveFileThreshold();
                boolean useArchive =
                        fileRetrievalPolicy.shouldUseArchive(orderedAddedOrModifiedFiles.size());
                Set<String> fetchedFilePaths;
                Set<String> presentFilePaths = Collections.emptySet();
                String fileFetchMode;
                if (useArchive) {
                    log.info("Using one repository archive at revision {} for {} incremental RAG files "
                                    + "(threshold: {})",
                            revision, orderedAddedOrModifiedFiles.size(), effectiveArchiveFileThreshold);
                    BranchArchiveService.ArchiveDirectorySnapshot archiveSnapshot =
                            branchArchiveService.downloadAndExtractSnapshotToDirectory(
                            vcsConnection,
                            workspaceSlug,
                            repoSlug,
                            revision,
                            new LinkedHashSet<>(orderedAddedOrModifiedFiles),
                            tempDir);
                    fetchedFilePaths = archiveSnapshot.extractedFiles();
                    presentFilePaths = archiveSnapshot.presentFiles();
                    fileFetchMode = "archive";
                } else {
                    log.info("Using per-file VCS retrieval for {} incremental RAG files (threshold: {})",
                            orderedAddedOrModifiedFiles.size(), effectiveArchiveFileThreshold);
                    PerFileFetchResult perFileResult = fetchFilesToTempDir(
                            vcsConnection,
                            workspaceSlug,
                            repoSlug,
                            revision,
                            orderedAddedOrModifiedFiles,
                            tempDir);
                    if (perFileResult.rateLimited()) {
                        log.warn("Per-file VCS retrieval hit a provider rate limit after {} of {} files; "
                                        + "switching the batch to one repository archive",
                                perFileResult.fetchedFiles().size(),
                                orderedAddedOrModifiedFiles.size());
                        BranchArchiveService.ArchiveDirectorySnapshot archiveSnapshot =
                                branchArchiveService.downloadAndExtractSnapshotToDirectory(
                                vcsConnection,
                                workspaceSlug,
                                repoSlug,
                                revision,
                                new LinkedHashSet<>(orderedAddedOrModifiedFiles),
                                tempDir);
                        fetchedFilePaths = archiveSnapshot.extractedFiles();
                        presentFilePaths = archiveSnapshot.presentFiles();
                        fileFetchMode = "archive-after-rate-limit";
                    } else {
                        fetchedFilePaths = perFileResult.fetchedFiles();
                        skippedNonTextFiles.addAll(perFileResult.skippedNonIndexableFiles());
                        orderedAddedOrModifiedFiles.removeAll(
                                perFileResult.skippedNonIndexableFiles());
                        fileFetchMode = "per-file";
                    }
                }
                Set<String> presentButNotText = orderedAddedOrModifiedFiles.stream()
                        .filter(presentFilePaths::contains)
                        .filter(path -> !fetchedFilePaths.contains(path))
                        .collect(Collectors.toCollection(LinkedHashSet::new));
                if (!presentButNotText.isEmpty()) {
                    skippedNonTextFiles.addAll(presentButNotText);
                    orderedAddedOrModifiedFiles.removeAll(presentButNotText);
                    log.warn("Skipping {} changed files that are present at revision {} but "
                                    + "cannot produce text chunks: {}",
                            presentButNotText.size(), revision,
                            String.join(", ", sortedList(presentButNotText)));
                }
                List<String> missingFiles = orderedAddedOrModifiedFiles.stream()
                        .filter(path -> !fetchedFilePaths.contains(path))
                        .toList();
                if (!missingFiles.isEmpty()) {
                    throw new IOException(
                            "Incremental RAG update aborted before mutation; "
                                    + "changed files were not fetched at revision "
                                    + revision + ": " + String.join(", ", missingFiles));
                }
                repoBase = tempDir.toString();
                result.put("updatedFiles", orderedAddedOrModifiedFiles.size());
                int appliedAddedFiles = (int) orderedAddedOrModifiedFiles.stream()
                        .filter(indexableAddedFiles::contains)
                        .count();
                result.put("addedFilesCount", appliedAddedFiles);
                result.put("skippedFiles", skippedNonTextFiles.size());
                result.put("fileFetchMode", fileFetchMode);
            }

            String changeSetRepoBase = repoBase;
            List<String> filesToUpdate = List.copyOf(orderedAddedOrModifiedFiles);
            Map<String, Object> updateResult = executeWithRetry(
                    "apply incremental RAG change set",
                    () -> ragPipelineClient.applyChanges(
                            filesToUpdate,
                            orderedDeletedFiles,
                            changeSetRepoBase,
                            projectWorkspace,
                            projectNamespace,
                            branch,
                            revision));
            result.put("deletedFiles", orderedDeletedFiles.size());
            result.putAll(updateResult);
            log.info(
                    "Applied one incremental RAG change set: {} updated, {} deleted, {} skipped",
                    filesToUpdate.size(),
                    orderedDeletedFiles.size(),
                    skippedNonTextFiles.size());
        } finally {
            if (tempDir != null) {
                deleteDirectory(tempDir.toFile());
            }
        }

        result.put("status", "completed");
        return result;
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

    private List<String> sortedList(Collection<String> values) {
        return values.stream()
                .filter(Objects::nonNull)
                .filter(value -> !value.isBlank())
                .sorted()
                .toList();
    }

    private Set<String> filterTextCandidates(
            Collection<String> paths,
            Set<String> skippedNonTextFiles) {
        Set<String> eligible = new LinkedHashSet<>();
        if (paths == null) {
            return eligible;
        }
        for (String path : paths) {
            if (TextFileEligibility.isTextCandidate(path)) {
                eligible.add(path);
            } else if (path != null && !path.isBlank()) {
                skippedNonTextFiles.add(path);
            }
        }
        return eligible;
    }

    private PerFileFetchResult fetchFilesToTempDir(
            VcsConnection vcsConnection,
            String workspaceSlug,
            String repoSlug,
            String branchOrCommit,
            List<String> filePaths,
            Path tempDir) throws IOException {
        VcsClient vcsClient = vcsClientProvider.getClient(vcsConnection);

        int workerCount = Math.min(Math.max(1, parallelRequests), filePaths.size());
        ExecutorService executor = Executors.newFixedThreadPool(workerCount);
        AtomicBoolean rateLimited = new AtomicBoolean(false);
        Set<String> skippedNonIndexableFiles = ConcurrentHashMap.newKeySet();
        try {
            List<CompletableFuture<Boolean>> futures = new ArrayList<>();

            for (String filePath : filePaths) {
                CompletableFuture<Boolean> future = CompletableFuture.supplyAsync(() -> {
                    if (rateLimited.get()) {
                        return false;
                    }
                    try {
                        String content = vcsClient.getFileContent(
                                workspaceSlug, repoSlug, filePath, branchOrCommit);
                        if (content != null) {
                            if (!TextFileEligibility.isBoundedTextContent(content)) {
                                skippedNonIndexableFiles.add(filePath);
                                log.warn(
                                        "Skipping provider file response that cannot produce bounded text: {}",
                                        filePath);
                                return false;
                            }
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
                        if (fileRetrievalPolicy.isRateLimited(e)) {
                            rateLimited.set(true);
                            log.warn("Rate limited while fetching {}; stopping new per-file VCS calls",
                                    filePath);
                        } else {
                            log.warn("Failed to fetch file {}: {}", filePath, e.getMessage());
                        }
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

            return new PerFileFetchResult(
                    fetchedFiles,
                    Set.copyOf(skippedNonIndexableFiles),
                    rateLimited.get());
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

    private record PerFileFetchResult(
            Set<String> fetchedFiles,
            Set<String> skippedNonIndexableFiles,
            boolean rateLimited) {
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
        // Track path-preserving copies separately from path-moving renames.
        String sourcePath = null;
        boolean copyOperation = false;

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
                sourcePath = null;
                copyOperation = false;
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
            } else if (line.startsWith("rename from ")) {
                sourcePath = line.substring("rename from ".length()).trim();
                copyOperation = false;
            } else if (line.startsWith("copy from ")) {
                sourcePath = line.substring("copy from ".length()).trim();
                copyOperation = true;
            } else if (line.startsWith("rename to ") || line.startsWith("copy to ")) {
                String renameTo = line.substring(line.indexOf(' ', line.indexOf(' ') + 1) + 1).trim();
                if (!copyOperation && sourcePath != null && !sourcePath.isEmpty()) {
                    deleted.add(sourcePath);
                }
                if (!renameTo.isEmpty()) {
                    added.add(renameTo);
                }
                fileProcessed = true;
                sourcePath = null;
                copyOperation = false;
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
