package org.rostilos.codecrow.pipelineagent.rag.service;

import org.rostilos.codecrow.pipelineagent.rag.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.*;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Service for managing RAG indexing operations.
 * Handles archive extraction and indexing coordination.
 */
@Service
public class RagIndexingService {
    private static final Logger log = LoggerFactory.getLogger(RagIndexingService.class);
    
    private final RagPipelineClient ragClient;

    public RagIndexingService(RagPipelineClient ragClient) {
        this.ragClient = ragClient;
    }

    /**
     * Index repository from archive (for initial full repository analysis)
     * 
     * @param archiveData ZIP archive containing repository files
     * @param projectWorkspace Workspace identifier
     * @param projectNamespace Project identifier
     * @param branch Branch name
     * @param commit Commit hash
     * @param excludePatterns List of glob patterns for paths to exclude from indexing
     * @return Indexing statistics
     * @deprecated Use {@link #indexFromArchiveFile} for large repositories to avoid OOM
     */
    @Deprecated
    public Map<String, Object> indexFromArchive(
            byte[] archiveData,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> excludePatterns
    ) throws IOException {
        log.info("Starting repository indexing from archive for {}/{}/{}", projectWorkspace, projectNamespace, branch);
        if (excludePatterns != null && !excludePatterns.isEmpty()) {
            log.info("Using {} custom exclude patterns", excludePatterns.size());
        }
        // Create temporary directory for extraction
        Path tempDir = Files.createTempDirectory("codecrow-rag-");
        try {
            // Extract archive
            extractArchive(archiveData, tempDir);
            
            // Index the extracted repository
            Map<String, Object> result = ragClient.indexRepository(
                    tempDir.toString(),
                    projectWorkspace,
                    projectNamespace,
                    branch,
                    commit,
                    excludePatterns
            );
            
            log.info("Repository indexed successfully: {}", result);
            return result;
            
        } finally {
            deleteDirectory(tempDir.toFile());
        }
    }

    /**
     * Index repository from archive file (streaming, memory-efficient).
     * 
     * @param archiveFile Path to the ZIP archive file
     * @param projectWorkspace Workspace identifier
     * @param projectNamespace Project identifier
     * @param branch Branch name
     * @param commit Commit hash
     * @param excludePatterns List of glob patterns for paths to exclude from indexing
     * @return Indexing statistics
     */
    public Map<String, Object> indexFromArchiveFile(
            Path archiveFile,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> excludePatterns
    ) throws IOException {
        log.info("Starting repository indexing from archive file for {}/{}/{}", projectWorkspace, projectNamespace, branch);
        if (excludePatterns != null && !excludePatterns.isEmpty()) {
            log.info("Using {} custom exclude patterns", excludePatterns.size());
        }
        // Create temporary directory for extraction
        Path tempDir = Files.createTempDirectory("codecrow-rag-");
        try {
            // Extract archive from file
            extractArchiveFile(archiveFile, tempDir);
            
            // Index the extracted repository
            Map<String, Object> result = ragClient.indexRepository(
                    tempDir.toString(),
                    projectWorkspace,
                    projectNamespace,
                    branch,
                    commit,
                    excludePatterns
            );
            
            log.info("Repository indexed successfully: {}", result);
            return result;
            
        } finally {
            deleteDirectory(tempDir.toFile());
        }
    }

    /**
     * Update index for specific changed files
     */
    public Map<String, Object> updateChangedFiles(
            List<String> filePaths,
            String repoPath,
            String workspace,
            String project,
            String branch,
            String commit
    ) throws IOException {
        log.info("Updating RAG index for {} files in {}/{}/{}", 
                filePaths.size(), workspace, project, branch);
        
        return ragClient.updateFiles(
                filePaths,
                repoPath,
                workspace,
                project,
                branch,
                commit
        );
    }

    /**
     * Index repository from local path (for branch analysis)
     */
    public Map<String, Object> indexRepository(
            String repoPath,
            String workspace,
            String project,
            String branch,
            String commit
    ) throws IOException {
        return indexRepository(repoPath, workspace, project, branch, commit, null);
    }
    
    /**
     * Index repository from local path with exclude patterns (for branch analysis)
     */
    public Map<String, Object> indexRepository(
            String repoPath,
            String workspace,
            String project,
            String branch,
            String commit,
            List<String> excludePatterns
    ) throws IOException {
        log.info("Indexing repository from path: {} for {}/{}/{}", 
                repoPath, workspace, project, branch);
        
        return ragClient.indexRepository(
                repoPath,
                workspace,
                project,
                branch,
                commit,
                excludePatterns
        );
    }

    /**
     * Check if RAG pipeline is available
     */
    public boolean isAvailable() {
        return ragClient.isHealthy();
    }

    /**
     * Extract ZIP archive to target directory
     */
    private void extractArchive(byte[] archiveData, Path targetDir) throws IOException {
        try (ByteArrayInputStream bais = new ByteArrayInputStream(archiveData);
             ZipInputStream zis = new ZipInputStream(bais)) {
            
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                Path entryPath = targetDir.resolve(entry.getName());
                
                // Security check: prevent path traversal
                if (!entryPath.normalize().startsWith(targetDir.normalize())) {
                    log.warn("Skipping potentially malicious entry: {}", entry.getName());
                    continue;
                }
                
                if (entry.isDirectory()) {
                    Files.createDirectories(entryPath);
                } else {
                    // Create parent directories if needed
                    Files.createDirectories(entryPath.getParent());
                    
                    // Extract file
                    try (FileOutputStream fos = new FileOutputStream(entryPath.toFile())) {
                        byte[] buffer = new byte[8192];
                        int len;
                        while ((len = zis.read(buffer)) > 0) {
                            fos.write(buffer, 0, len);
                        }
                    }
                }
                zis.closeEntry();
            }
        }
        
        log.info("Extracted archive to: {}", targetDir);
    }

    /**
     * Extract ZIP archive file to target directory (streaming, memory-efficient)
     */
    private void extractArchiveFile(Path archiveFile, Path targetDir) throws IOException {
        try (InputStream fis = Files.newInputStream(archiveFile);
             ZipInputStream zis = new ZipInputStream(fis)) {
            
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                Path entryPath = targetDir.resolve(entry.getName());
                
                // Security check: prevent path traversal
                if (!entryPath.normalize().startsWith(targetDir.normalize())) {
                    log.warn("Skipping potentially malicious entry: {}", entry.getName());
                    continue;
                }
                
                if (entry.isDirectory()) {
                    Files.createDirectories(entryPath);
                } else {
                    // Create parent directories if needed
                    Files.createDirectories(entryPath.getParent());
                    
                    // Extract file
                    try (FileOutputStream fos = new FileOutputStream(entryPath.toFile())) {
                        byte[] buffer = new byte[8192];
                        int len;
                        while ((len = zis.read(buffer)) > 0) {
                            fos.write(buffer, 0, len);
                        }
                    }
                }
                zis.closeEntry();
            }
        }
        
        log.info("Extracted archive file {} to: {}", archiveFile, targetDir);
    }

    /**
     * Recursively delete directory and its contents
     */
    private void deleteDirectory(File dir) {
        if (dir.exists()) {
            File[] files = dir.listFiles();
            if (files != null) {
                for (File file : files) {
                    if (file.isDirectory()) {
                        deleteDirectory(file);
                    } else {
                        if (!file.delete()) {
                            log.warn("Failed to delete file: {}", file.getAbsolutePath());
                        }
                    }
                }
            }
            if (!dir.delete()) {
                log.warn("Failed to delete directory: {}", dir.getAbsolutePath());
            }
        }
    }
}

