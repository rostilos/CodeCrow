package org.rostilos.codecrow.analysisengine.service;

import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Service that downloads a VCS branch/commit archive and extracts file contents.
 * <p>
 * Replaces per-file VCS API calls (which cause rate-limiting, e.g. Bitbucket HTTP 429)
 * with a single archive download followed by local extraction of only the needed files.
 * <p>
 * Uses the same VCS archive endpoints as the RAG indexing pipeline
 * ({@link VcsClient#downloadRepositoryArchiveToFile}).
 */
@Service
public class BranchArchiveService {

    private static final Logger log = LoggerFactory.getLogger(BranchArchiveService.class);

    /**
     * Maximum single-file size to extract from archive (10 MB).
     * Files larger than this are skipped to avoid memory pressure.
     */
    private static final long MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024;

    private final VcsClientProvider vcsClientProvider;

    public BranchArchiveService(VcsClientProvider vcsClientProvider) {
        this.vcsClientProvider = vcsClientProvider;
    }

    /**
     * Downloads a branch/commit archive and extracts the contents of specified files.
     * <p>
     * If {@code neededFiles} is non-null and non-empty, only files whose relative path
     * matches an entry in the set are extracted.  Other files are skipped, saving memory.
     * <p>
     * Binary files (detected by null bytes in the first 1 KB) are skipped.
     * <p>
     * The returned map uses repository-relative paths as keys (e.g.
     * {@code "src/main/App.java"}) with the VCS-provider archive root directory
     * stripped automatically.
     *
     * @param vcsConnection  authenticated VCS connection
     * @param workspace      workspace / owner / namespace
     * @param repoSlug       repository slug
     * @param branchOrCommit branch name or commit hash to download
     * @param neededFiles    set of relative file paths to extract ({@code null} = extract all text files)
     * @return map of relativePath → file content (UTF-8 text files only)
     * @throws IOException if the archive download or extraction fails
     */
    public Map<String, String> downloadAndExtractFiles(
            VcsConnection vcsConnection,
            String workspace,
            String repoSlug,
            String branchOrCommit,
            Set<String> neededFiles
    ) throws IOException {
        Map<String, String> results = new LinkedHashMap<>();
        downloadAndProcessArchive(
                vcsConnection,
                workspace,
                repoSlug,
                branchOrCommit,
                archiveFile -> extractFilesFromArchive(
                        archiveFile,
                        neededFiles,
                        (relativePath, bytes) -> {
                            results.put(relativePath, new String(bytes, StandardCharsets.UTF_8));
                            return true;
                        }));
        return results;
    }

    /**
     * Downloads a branch/commit archive and extracts the requested text files directly
     * into an isolated local directory.
     * <p>
     * This variant is intended for consumers such as incremental RAG indexing that
     * already operate on local paths. It avoids retaining all requested file contents
     * in memory and guarantees that archive entries cannot escape {@code targetDirectory}.
     *
     * @param vcsConnection   authenticated VCS connection
     * @param workspace       workspace / owner / namespace
     * @param repoSlug        repository slug
     * @param branchOrCommit  branch name or commit hash to download
     * @param neededFiles     relative file paths to extract ({@code null} = all text files)
     * @param targetDirectory isolated directory that will receive repository-relative files
     * @return repository-relative paths successfully extracted into the target directory
     * @throws IOException if the archive download or extraction fails
     */
    public Set<String> downloadAndExtractFilesToDirectory(
            VcsConnection vcsConnection,
            String workspace,
            String repoSlug,
            String branchOrCommit,
            Set<String> neededFiles,
            Path targetDirectory
    ) throws IOException {
        Objects.requireNonNull(targetDirectory, "targetDirectory");
        Path normalizedTarget = targetDirectory.toAbsolutePath().normalize();
        Files.createDirectories(normalizedTarget);
        setWorldReadable(normalizedTarget, true);

        return downloadAndProcessArchive(
                vcsConnection,
                workspace,
                repoSlug,
                branchOrCommit,
                archiveFile -> extractFilesFromArchive(
                        archiveFile,
                        neededFiles,
                        (relativePath, bytes) -> writeFile(normalizedTarget, relativePath, bytes)));
    }

    private <T> T downloadAndProcessArchive(
            VcsConnection vcsConnection,
            String workspace,
            String repoSlug,
            String branchOrCommit,
            ArchiveProcessor<T> processor
    ) throws IOException {
        VcsClient client = vcsClientProvider.getClient(vcsConnection);
        Path tempFile = Files.createTempFile("codecrow-branch-archive-", ".zip");

        try {
            long archiveSize = client.downloadRepositoryArchiveToFile(
                    workspace, repoSlug, branchOrCommit, tempFile);

            String shortRef = branchOrCommit.length() > 12
                    ? branchOrCommit.substring(0, 12) + "…"
                    : branchOrCommit;
            log.info("Downloaded branch archive: {} ({}/{} @ {})",
                    formatBytes(archiveSize), workspace, repoSlug, shortRef);

            return processor.process(tempFile);
        } finally {
            try {
                Files.deleteIfExists(tempFile);
            } catch (IOException e) {
                log.warn("Failed to delete temp archive file: {}", tempFile);
            }
        }
    }

    // ──────────────────────────────────────────────────────────────────────────
    //  Internal helpers
    // ──────────────────────────────────────────────────────────────────────────

    /**
     * Streams through the zip archive, extracting only the needed files.
     * Strips the VCS-provider archive root prefix from paths.
     */
    private Set<String> extractFilesFromArchive(
            Path archiveFile,
            Set<String> neededFiles,
            ExtractedFileConsumer consumer
    ) throws IOException {
        Set<String> extractedFiles = new LinkedHashSet<>();
        int skippedBinary = 0;
        int skippedLarge = 0;
        int skippedNotNeeded = 0;
        int skippedUnsafe = 0;

        try (ZipInputStream zis = new ZipInputStream(Files.newInputStream(archiveFile))) {
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                if (entry.isDirectory()) {
                    zis.closeEntry();
                    continue;
                }

                String relativePath = stripArchiveRoot(entry.getName());

                // Skip files we don't need
                if (neededFiles != null && !neededFiles.isEmpty()
                        && !neededFiles.contains(relativePath)) {
                    skippedNotNeeded++;
                    zis.closeEntry();
                    continue;
                }

                // Read the entry content
                byte[] bytes = readZipEntry(zis);

                // Skip very large files
                if (bytes.length > MAX_FILE_SIZE_BYTES) {
                    log.debug("Skipping large file {} ({} bytes)", relativePath, bytes.length);
                    skippedLarge++;
                    zis.closeEntry();
                    continue;
                }

                // Skip binary files (null bytes in first 1 KB)
                if (isBinary(bytes)) {
                    skippedBinary++;
                    zis.closeEntry();
                    continue;
                }

                if (consumer.accept(relativePath, bytes)) {
                    extractedFiles.add(relativePath);
                } else {
                    skippedUnsafe++;
                }
                zis.closeEntry();

                // Early exit: if we have all needed files, stop scanning the archive
                if (neededFiles != null && !neededFiles.isEmpty()
                        && extractedFiles.size() >= neededFiles.size()) {
                    break;
                }
            }
        }

        log.info("Archive extraction: {} files extracted, {} skipped (not needed), "
                        + "{} binary, {} too large, {} unsafe. Requested: {}",
                extractedFiles.size(), skippedNotNeeded, skippedBinary, skippedLarge, skippedUnsafe,
                neededFiles != null ? neededFiles.size() : "all");

        return extractedFiles;
    }

    private boolean writeFile(Path targetDirectory, String relativePath, byte[] bytes) throws IOException {
        Path targetFile;
        try {
            targetFile = targetDirectory.resolve(relativePath).normalize();
        } catch (RuntimeException e) {
            log.warn("Skipping invalid archive entry path: {}", relativePath);
            return false;
        }
        if (!targetFile.startsWith(targetDirectory) || targetFile.equals(targetDirectory)) {
            log.warn("Skipping archive entry outside target directory: {}", relativePath);
            return false;
        }

        Path parent = targetFile.getParent();
        Files.createDirectories(parent);
        for (Path directory = parent;
             directory != null && directory.startsWith(targetDirectory);
             directory = directory.getParent()) {
            setWorldReadable(directory, true);
        }
        Files.write(targetFile, bytes);
        setWorldReadable(targetFile, false);
        return true;
    }

    private void setWorldReadable(Path path, boolean directory) {
        File file = path.toFile();
        file.setReadable(true, false);
        if (directory) {
            file.setExecutable(true, false);
        }
    }

    /**
     * Strips the VCS-provider archive root directory from a zip entry path.
     * <p>
     * VCS archives wrap all files in a root directory whose name varies by provider:
     * <ul>
     *   <li>Bitbucket: {@code {workspace}-{repo}-{hash}/path/to/file}</li>
     *   <li>GitHub:    {@code {owner}-{repo}-{hash}/path/to/file}</li>
     *   <li>GitLab:    {@code {repo}-{branch}-{hash}/path/to/file}</li>
     * </ul>
     * This method strips the first path component (everything up to and including
     * the first {@code /}).
     */
    static String stripArchiveRoot(String entryPath) {
        int firstSlash = entryPath.indexOf('/');
        if (firstSlash >= 0 && firstSlash < entryPath.length() - 1) {
            return entryPath.substring(firstSlash + 1);
        }
        return entryPath;
    }

    private byte[] readZipEntry(ZipInputStream zis) throws IOException {
        ByteArrayOutputStream baos = new ByteArrayOutputStream(8192);
        byte[] buffer = new byte[8192];
        int len;
        while ((len = zis.read(buffer)) > 0) {
            baos.write(buffer, 0, len);
        }
        return baos.toByteArray();
    }

    /**
     * Checks if the data appears to be binary by looking for null bytes
     * in the first 1024 bytes.
     */
    private boolean isBinary(byte[] data) {
        int checkLimit = Math.min(data.length, 1024);
        for (int i = 0; i < checkLimit; i++) {
            if (data[i] == 0) return true;
        }
        return false;
    }

    private String formatBytes(long bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return String.format("%.1f KB", bytes / 1024.0);
        return String.format("%.1f MB", bytes / (1024.0 * 1024.0));
    }

    @FunctionalInterface
    private interface ArchiveProcessor<T> {
        T process(Path archiveFile) throws IOException;
    }

    @FunctionalInterface
    private interface ExtractedFileConsumer {
        boolean accept(String relativePath, byte[] bytes) throws IOException;
    }
}
