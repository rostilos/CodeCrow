package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
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

@Service
public class RagIndexingService {
    private static final Logger log = LoggerFactory.getLogger(RagIndexingService.class);
    
    private final RagPipelineClient ragClient;

    public RagIndexingService(RagPipelineClient ragClient) {
        this.ragClient = ragClient;
    }

    @Deprecated
    public Map<String, Object> indexFromArchive(
            byte[] archiveData,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> includePatterns,
            List<String> excludePatterns
    ) throws IOException {
        log.info("Starting repository indexing from archive for {}/{}/{}", projectWorkspace, projectNamespace, branch);
        if (includePatterns != null && !includePatterns.isEmpty()) {
            log.info("Using {} custom include patterns", includePatterns.size());
        }
        if (excludePatterns != null && !excludePatterns.isEmpty()) {
            log.info("Using {} custom exclude patterns", excludePatterns.size());
        }
        Path tempDir = Files.createTempDirectory("codecrow-rag-");
        try {
            extractArchive(archiveData, tempDir);
            
            Map<String, Object> result = ragClient.indexRepository(
                    tempDir.toString(),
                    projectWorkspace,
                    projectNamespace,
                    branch,
                    commit,
                    includePatterns,
                    excludePatterns
            );
            
            log.info("Repository indexed successfully: {}", result);
            return result;
            
        } finally {
            deleteDirectory(tempDir.toFile());
        }
    }

    public Map<String, Object> indexFromArchiveFile(
            Path archiveFile,
            String projectWorkspace,
            String projectNamespace,
            String branch,
            String commit,
            List<String> includePatterns,
            List<String> excludePatterns
    ) throws IOException {
        log.info("Starting repository indexing from archive file for {}/{}/{}", projectWorkspace, projectNamespace, branch);
        if (includePatterns != null && !includePatterns.isEmpty()) {
            log.info("Using {} custom include patterns", includePatterns.size());
        }
        if (excludePatterns != null && !excludePatterns.isEmpty()) {
            log.info("Using {} custom exclude patterns", excludePatterns.size());
        }
        Path tempDir = Files.createTempDirectory("codecrow-rag-");
        try {
            extractArchiveFile(archiveFile, tempDir);
            
            Map<String, Object> result = ragClient.indexRepository(
                    tempDir.toString(),
                    projectWorkspace,
                    projectNamespace,
                    branch,
                    commit,
                    includePatterns,
                    excludePatterns
            );
            
            log.info("Repository indexed successfully: {}", result);
            return result;
            
        } finally {
            deleteDirectory(tempDir.toFile());
        }
    }

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

    public Map<String, Object> indexRepository(
            String repoPath,
            String workspace,
            String project,
            String branch,
            String commit
    ) throws IOException {
        return indexRepository(repoPath, workspace, project, branch, commit, null, null);
    }
    
    public Map<String, Object> indexRepository(
            String repoPath,
            String workspace,
            String project,
            String branch,
            String commit,
            List<String> includePatterns,
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
                includePatterns,
                excludePatterns
        );
    }

    public boolean isAvailable() {
        return ragClient.isHealthy();
    }

    private void extractArchive(byte[] archiveData, Path targetDir) throws IOException {
        try (ByteArrayInputStream bais = new ByteArrayInputStream(archiveData);
             ZipInputStream zis = new ZipInputStream(bais)) {
            
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                Path entryPath = targetDir.resolve(entry.getName());
                
                if (!entryPath.normalize().startsWith(targetDir.normalize())) {
                    log.warn("Skipping potentially malicious entry: {}", entry.getName());
                    continue;
                }
                
                if (entry.isDirectory()) {
                    Files.createDirectories(entryPath);
                } else {
                    Files.createDirectories(entryPath.getParent());
                    
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

    private void extractArchiveFile(Path archiveFile, Path targetDir) throws IOException {
        try (InputStream fis = Files.newInputStream(archiveFile);
             ZipInputStream zis = new ZipInputStream(fis)) {
            
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                Path entryPath = targetDir.resolve(entry.getName());
                
                if (!entryPath.normalize().startsWith(targetDir.normalize())) {
                    log.warn("Skipping potentially malicious entry: {}", entry.getName());
                    continue;
                }
                
                if (entry.isDirectory()) {
                    Files.createDirectories(entryPath);
                } else {
                    Files.createDirectories(entryPath.getParent());
                    
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
