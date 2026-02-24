package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.codeanalysis.AnalyzedFileContent;
import org.rostilos.codecrow.core.model.codeanalysis.AnalyzedFileSnapshot;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.AnalyzedFileContentRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.AnalyzedFileSnapshotRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Service for persisting analyzed file contents with content-addressed deduplication.
 * <p>
 * Each unique file content (identified by SHA-256 hash) is stored once in {@code analyzed_file_content}.
 * Each analysis references its file contents via {@code analyzed_file_snapshot} rows, allowing
 * multiple analyses to share the same content blob when files haven't changed.
 */
@Service
@Transactional
public class FileSnapshotService {

    private static final Logger log = LoggerFactory.getLogger(FileSnapshotService.class);

    private final AnalyzedFileContentRepository contentRepository;
    private final AnalyzedFileSnapshotRepository snapshotRepository;

    public FileSnapshotService(
            AnalyzedFileContentRepository contentRepository,
            AnalyzedFileSnapshotRepository snapshotRepository
    ) {
        this.contentRepository = contentRepository;
        this.snapshotRepository = snapshotRepository;
    }

    /**
     * Persist file contents for an analysis.
     * Deduplicates content blobs by SHA-256 hash and creates snapshot references.
     *
     * @param analysis     the analysis to attach snapshots to
     * @param fileContents map of filePath → raw file content
     * @param commitHash   the commit these files were fetched from
     * @return the number of snapshots created
     */
    public int persistSnapshots(CodeAnalysis analysis, Map<String, String> fileContents, String commitHash) {
        if (fileContents == null || fileContents.isEmpty()) {
            return 0;
        }

        int created = 0;
        for (Map.Entry<String, String> entry : fileContents.entrySet()) {
            String filePath = entry.getKey();
            String content = entry.getValue();

            if (content == null || filePath == null || filePath.isBlank()) {
                continue;
            }

            try {
                // Deduplicate content
                String contentHash = sha256(content);
                AnalyzedFileContent fileContent = contentRepository.findByContentHash(contentHash)
                        .orElseGet(() -> {
                            AnalyzedFileContent newContent = new AnalyzedFileContent();
                            newContent.setContentHash(contentHash);
                            newContent.setContent(content);
                            newContent.setSizeBytes(content.getBytes(StandardCharsets.UTF_8).length);
                            newContent.setLineCount(countLines(content));
                            return contentRepository.save(newContent);
                        });

                // Create snapshot reference (skip if already exists for this analysis+path)
                Optional<AnalyzedFileSnapshot> existing = snapshotRepository
                        .findByAnalysisIdAndFilePath(analysis.getId(), filePath);
                if (existing.isPresent()) {
                    log.debug("Snapshot already exists for analysis={}, file={}", analysis.getId(), filePath);
                    continue;
                }

                AnalyzedFileSnapshot snapshot = new AnalyzedFileSnapshot();
                snapshot.setAnalysis(analysis);
                snapshot.setFilePath(filePath);
                snapshot.setFileContent(fileContent);
                snapshot.setCommitHash(commitHash);
                snapshotRepository.save(snapshot);
                created++;

            } catch (Exception e) {
                log.warn("Failed to persist snapshot for file '{}' in analysis {}: {}",
                        filePath, analysis.getId(), e.getMessage());
            }
        }

        log.info("Persisted {} file snapshots for analysis {} ({} files provided)",
                created, analysis.getId(), fileContents.size());
        return created;
    }

    /**
     * Update existing file snapshots or create new ones for an analysis.
     * Unlike {@link #persistSnapshots}, this method updates the content reference
     * when a snapshot already exists for the given (analysis, filePath) pair.
     * Used by branch analysis to refresh file content after code changes.
     *
     * @param analysis     the analysis to update snapshots for
     * @param fileContents map of filePath → raw file content
     * @param commitHash   the commit these files were fetched from
     * @return the number of snapshots created or updated
     */
    public int updateOrPersistSnapshots(CodeAnalysis analysis, Map<String, String> fileContents, String commitHash) {
        if (fileContents == null || fileContents.isEmpty()) {
            return 0;
        }

        int updated = 0;
        for (Map.Entry<String, String> entry : fileContents.entrySet()) {
            String filePath = entry.getKey();
            String content = entry.getValue();

            if (content == null || filePath == null || filePath.isBlank()) {
                continue;
            }

            try {
                // Deduplicate content
                String contentHash = sha256(content);
                AnalyzedFileContent fileContent = contentRepository.findByContentHash(contentHash)
                        .orElseGet(() -> {
                            AnalyzedFileContent newContent = new AnalyzedFileContent();
                            newContent.setContentHash(contentHash);
                            newContent.setContent(content);
                            newContent.setSizeBytes(content.getBytes(StandardCharsets.UTF_8).length);
                            newContent.setLineCount(countLines(content));
                            return contentRepository.save(newContent);
                        });

                Optional<AnalyzedFileSnapshot> existingOpt = snapshotRepository
                        .findByAnalysisIdAndFilePath(analysis.getId(), filePath);
                if (existingOpt.isPresent()) {
                    // Update existing snapshot with new content
                    AnalyzedFileSnapshot existing = existingOpt.get();
                    if (!contentHash.equals(existing.getFileContent().getContentHash())) {
                        existing.setFileContent(fileContent);
                        existing.setCommitHash(commitHash);
                        snapshotRepository.save(existing);
                        updated++;
                        log.debug("Updated snapshot for analysis={}, file={} (content changed)",
                                analysis.getId(), filePath);
                    }
                } else {
                    // Create new snapshot
                    AnalyzedFileSnapshot snapshot = new AnalyzedFileSnapshot();
                    snapshot.setAnalysis(analysis);
                    snapshot.setFilePath(filePath);
                    snapshot.setFileContent(fileContent);
                    snapshot.setCommitHash(commitHash);
                    snapshotRepository.save(snapshot);
                    updated++;
                }
            } catch (Exception e) {
                log.warn("Failed to update snapshot for file '{}' in analysis {}: {}",
                        filePath, analysis.getId(), e.getMessage());
            }
        }

        if (updated > 0) {
            log.info("Updated {} file snapshots for analysis {} ({} files provided)",
                    updated, analysis.getId(), fileContents.size());
        }
        return updated;
    }

    /**
     * Retrieve all file snapshots for an analysis with content eagerly loaded.
     */
    public List<AnalyzedFileSnapshot> getSnapshotsWithContent(Long analysisId) {
        return snapshotRepository.findByAnalysisIdWithContent(analysisId);
    }

    /**
     * Retrieve the file content for a specific file in a specific analysis.
     *
     * @return the raw file content, or empty if not found
     */
    public Optional<String> getFileContent(Long analysisId, String filePath) {
        return snapshotRepository.findByAnalysisIdAndFilePathWithContent(analysisId, filePath)
                .map(snapshot -> snapshot.getFileContent().getContent());
    }

    /**
     * Build a filePath → content map from stored snapshots for an analysis.
     * Useful for re-computing line hashes during tracking.
     */
    public Map<String, String> getFileContentsMap(Long analysisId) {
        List<AnalyzedFileSnapshot> snapshots = snapshotRepository.findByAnalysisIdWithContent(analysisId);
        Map<String, String> map = new java.util.HashMap<>();
        for (AnalyzedFileSnapshot s : snapshots) {
            map.put(s.getFilePath(), s.getFileContent().getContent());
        }
        return map;
    }

    /**
     * Get snapshot metadata (without content) for an analysis.
     */
    public List<AnalyzedFileSnapshot> getSnapshots(Long analysisId) {
        return snapshotRepository.findByAnalysisId(analysisId);
    }

    // ── Internal helpers ─────────────────────────────────────────────────

    private static String sha256(String input) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(input.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(64);
            for (byte b : hash) {
                hex.append(String.format("%02x", b));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException("SHA-256 not available", e);
        }
    }

    private static int countLines(String content) {
        if (content == null || content.isEmpty()) return 0;
        int lines = 1;
        for (int i = 0; i < content.length(); i++) {
            if (content.charAt(i) == '\n') lines++;
        }
        return lines;
    }
}
