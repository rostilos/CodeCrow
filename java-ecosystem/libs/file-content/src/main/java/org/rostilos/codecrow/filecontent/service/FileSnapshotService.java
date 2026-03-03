package org.rostilos.codecrow.filecontent.service;

import org.rostilos.codecrow.core.model.branch.Branch;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.filecontent.model.AnalyzedFileContent;
import org.rostilos.codecrow.filecontent.model.AnalyzedFileSnapshot;
import org.rostilos.codecrow.filecontent.persistence.AnalyzedFileContentRepository;
import org.rostilos.codecrow.filecontent.persistence.AnalyzedFileSnapshotRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
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
    private final BranchRepository branchRepository;

    public FileSnapshotService(
            AnalyzedFileContentRepository contentRepository,
            AnalyzedFileSnapshotRepository snapshotRepository,
            BranchRepository branchRepository
    ) {
        this.contentRepository = contentRepository;
        this.snapshotRepository = snapshotRepository;
        this.branchRepository = branchRepository;
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
                log.error("Failed to persist snapshot for file '{}' in analysis {}; "
                        + "aborting remaining files to avoid corrupted session (HHH000099). Cause: {}",
                        filePath, analysis.getId(), e.getMessage(), e);
                break; // Session is unusable after a persistence exception
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
                log.error("Failed to update snapshot for file '{}' in analysis {}; "
                        + "aborting remaining files to avoid corrupted session (HHH000099). Cause: {}",
                        filePath, analysis.getId(), e.getMessage(), e);
                break; // Session is unusable after a persistence exception
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

    // ── PR-level persistence ─────────────────────────────────────────────

    /**
     * Persist / accumulate file snapshots at the <b>pull request</b> level.
     * <p>
     * For each file in {@code fileContents}:
     * <ul>
     *   <li>If a snapshot already exists for (PR, filePath) and the content hash differs → update it.</li>
     *   <li>If no snapshot exists → create one.</li>
     *   <li>Previously-stored files from earlier iterations remain untouched.</li>
     * </ul>
     * This ensures that after multiple PR iterations the source viewer shows <b>all</b>
     * files ever analysed, not just the files from the latest run.
     *
     * @param pullRequest  the PR to attach snapshots to
     * @param analysis     the current analysis (also stored on the snapshot for back-reference)
     * @param fileContents map of filePath → raw file content
     * @param commitHash   the commit these files were fetched from
     * @return the number of snapshots created or updated
     */
    public int persistSnapshotsForPr(PullRequest pullRequest, CodeAnalysis analysis,
                                     Map<String, String> fileContents, String commitHash) {
        if (fileContents == null || fileContents.isEmpty()) {
            return 0;
        }

        int changed = 0;
        for (Map.Entry<String, String> entry : fileContents.entrySet()) {
            String filePath = entry.getKey();
            String content = entry.getValue();

            if (content == null || filePath == null || filePath.isBlank()) {
                continue;
            }

            try {
                // Content-addressed dedup
                String contentHash = sha256(content);
                AnalyzedFileContent fileContent = contentRepository.findByContentHash(contentHash)
                        .orElseGet(() -> {
                            AnalyzedFileContent nc = new AnalyzedFileContent();
                            nc.setContentHash(contentHash);
                            nc.setContent(content);
                            nc.setSizeBytes(content.getBytes(StandardCharsets.UTF_8).length);
                            nc.setLineCount(countLines(content));
                            return contentRepository.save(nc);
                        });

                Optional<AnalyzedFileSnapshot> existingOpt =
                        snapshotRepository.findByPullRequestIdAndFilePath(pullRequest.getId(), filePath);

                if (existingOpt.isPresent()) {
                    AnalyzedFileSnapshot existing = existingOpt.get();
                    // Update content only when it changed
                    if (!contentHash.equals(existing.getFileContent().getContentHash())) {
                        existing.setFileContent(fileContent);
                        existing.setCommitHash(commitHash);
                        existing.setAnalysis(analysis);
                        snapshotRepository.save(existing);
                        changed++;
                        log.debug("Updated PR snapshot for PR={}, file={}", pullRequest.getId(), filePath);
                    }
                } else {
                    AnalyzedFileSnapshot snapshot = new AnalyzedFileSnapshot();
                    snapshot.setPullRequest(pullRequest);
                    snapshot.setAnalysis(analysis);      // back-reference for traceability
                    snapshot.setFilePath(filePath);
                    snapshot.setFileContent(fileContent);
                    snapshot.setCommitHash(commitHash);
                    snapshotRepository.save(snapshot);
                    changed++;
                }
            } catch (Exception e) {
                log.error("Failed to persist PR snapshot for file '{}' in PR {}; "
                        + "aborting remaining files to avoid corrupted session (HHH000099). Cause: {}",
                        filePath, pullRequest.getId(), e.getMessage(), e);
                break; // Session is unusable after a persistence exception
            }
        }

        log.info("Persisted {} PR-level file snapshots for PR {} (analysis={}, {} files provided)",
                changed, pullRequest.getId(), analysis.getId(), fileContents.size());
        return changed;
    }

    // ── Branch-level persistence (direct FK) ───────────────────────────

    /**
     * Persist / update file snapshots at the <b>branch</b> level using the direct branch_id FK.
     * <p>
     * For each file in {@code fileContents}:
     * <ul>
     *   <li>If a snapshot already exists for (branch_id, filePath) and the content hash differs → update it.</li>
     *   <li>If no snapshot exists → create one.</li>
     * </ul>
     * This upsert model means each branch has exactly one snapshot per file path,
     * always pointing to the latest content.
     *
     * @param branch       the branch entity to attach snapshots to
     * @param fileContents map of filePath → raw file content
     * @param commitHash   the commit these files were fetched from
     * @return the number of snapshots created or updated
     */
    public int persistSnapshotsForBranch(Branch branch, Map<String, String> fileContents, String commitHash) {
        if (fileContents == null || fileContents.isEmpty()) {
            return 0;
        }

        int changed = 0;
        for (Map.Entry<String, String> entry : fileContents.entrySet()) {
            String filePath = entry.getKey();
            String content = entry.getValue();

            if (content == null || filePath == null || filePath.isBlank()) {
                continue;
            }

            try {
                // Content-addressed dedup
                String contentHash = sha256(content);
                AnalyzedFileContent fileContent = contentRepository.findByContentHash(contentHash)
                        .orElseGet(() -> {
                            AnalyzedFileContent nc = new AnalyzedFileContent();
                            nc.setContentHash(contentHash);
                            nc.setContent(content);
                            nc.setSizeBytes(content.getBytes(StandardCharsets.UTF_8).length);
                            nc.setLineCount(countLines(content));
                            return contentRepository.save(nc);
                        });

                Optional<AnalyzedFileSnapshot> existingOpt =
                        snapshotRepository.findByBranchIdAndFilePath(branch.getId(), filePath);

                if (existingOpt.isPresent()) {
                    AnalyzedFileSnapshot existing = existingOpt.get();
                    // Update content only when it changed
                    if (!contentHash.equals(existing.getFileContent().getContentHash())) {
                        existing.setFileContent(fileContent);
                        existing.setCommitHash(commitHash);
                        snapshotRepository.save(existing);
                        changed++;
                        log.debug("Updated branch snapshot for branch={}, file={}", branch.getId(), filePath);
                    }
                } else {
                    AnalyzedFileSnapshot snapshot = new AnalyzedFileSnapshot();
                    snapshot.setBranch(branch);
                    snapshot.setFilePath(filePath);
                    snapshot.setFileContent(fileContent);
                    snapshot.setCommitHash(commitHash);
                    snapshotRepository.save(snapshot);
                    changed++;
                }
            } catch (Exception e) {
                log.error("Failed to persist branch snapshot for file '{}' in branch {}; "
                        + "aborting remaining files to avoid corrupted session (HHH000099). Cause: {}",
                        filePath, branch.getId(), e.getMessage(), e);
                break; // Session is unusable after a persistence exception
            }
        }

        if (changed > 0) {
            log.info("Persisted {} branch-level file snapshots for branch {} ({} files provided)",
                    changed, branch.getId(), fileContents.size());
        }
        return changed;
    }

    // ── Branch-level retrieval (direct FK) ───────────────────────────────

    /**
     * Retrieve all file snapshots for a branch using the direct branch_id FK.
     * Returns metadata only.
     */
    public List<AnalyzedFileSnapshot> getSnapshotsForBranchById(Long branchId) {
        return snapshotRepository.findByBranchId(branchId);
    }

    /**
     * Retrieve all file snapshots for a branch with content eagerly loaded.
     */
    public List<AnalyzedFileSnapshot> getSnapshotsWithContentForBranch(Long branchId) {
        return snapshotRepository.findByBranchIdWithContent(branchId);
    }

    /**
     * Retrieve the raw file content for a specific file in a branch using the direct FK.
     */
    public Optional<String> getFileContentForBranchById(Long branchId, String filePath) {
        return snapshotRepository.findByBranchIdAndFilePathWithContent(branchId, filePath)
                .map(s -> s.getFileContent().getContent());
    }

    /**
     * Build a filePath → content map from stored branch-level snapshots.
     */
    public Map<String, String> getFileContentsMapForBranch(Long branchId) {
        List<AnalyzedFileSnapshot> snapshots = snapshotRepository.findByBranchIdWithContent(branchId);
        Map<String, String> map = new java.util.HashMap<>();
        for (AnalyzedFileSnapshot s : snapshots) {
            map.put(s.getFilePath(), s.getFileContent().getContent());
        }
        return map;
    }

    // ── PR-level retrieval ───────────────────────────────────────────────

    /**
     * Retrieve all file snapshots accumulated for a PR with content eagerly loaded.
     */
    public List<AnalyzedFileSnapshot> getSnapshotsWithContentForPr(Long pullRequestId) {
        return snapshotRepository.findByPullRequestIdWithContent(pullRequestId);
    }

    /**
     * Retrieve the raw file content for a specific file in a PR.
     */
    public Optional<String> getFileContentForPr(Long pullRequestId, String filePath) {
        return snapshotRepository.findByPullRequestIdAndFilePathWithContent(pullRequestId, filePath)
                .map(s -> s.getFileContent().getContent());
    }

    /**
     * Build a filePath → content map from stored PR-level snapshots.
     */
    public Map<String, String> getFileContentsMapForPr(Long pullRequestId) {
        List<AnalyzedFileSnapshot> snapshots = snapshotRepository.findByPullRequestIdWithContent(pullRequestId);
        Map<String, String> map = new java.util.HashMap<>();
        for (AnalyzedFileSnapshot s : snapshots) {
            map.put(s.getFilePath(), s.getFileContent().getContent());
        }
        return map;
    }

    /**
     * Get snapshot metadata (without content) for a PR.
     */
    public List<AnalyzedFileSnapshot> getSnapshotsForPr(Long pullRequestId) {
        return snapshotRepository.findByPullRequestId(pullRequestId);
    }

    // ── Branch-level aggregated retrieval ────────────────────────────────

    /**
     * Get the latest file snapshots for a branch.
     * <p>
     * Merges two snapshot sources to ensure ALL ever-analysed files are visible:
     * <ol>
     *   <li><b>Branch-level snapshots</b> (direct branch_id FK) — created by
     *       {@link #persistSnapshotsForBranch} during each analysis run. These only
     *       cover files that appeared in a diff scope.</li>
     *   <li><b>Legacy analysis-level snapshots</b> (via analysis_id + DISTINCT ON) — cover
     *       all files from prior analyses that used the older code path.</li>
     * </ol>
     * Branch-level snapshots take precedence when both exist for the same file path.
     * Returns metadata only (no content loaded).
     */
    public List<AnalyzedFileSnapshot> getSnapshotsForBranch(Long projectId, String branchName) {
        Map<String, AnalyzedFileSnapshot> snapshotsByPath = new LinkedHashMap<>();

        // 1. Branch-level snapshots (highest priority — latest content)
        Optional<Branch> branchOpt = branchRepository.findByProjectIdAndBranchName(projectId, branchName);
        if (branchOpt.isPresent()) {
            List<AnalyzedFileSnapshot> direct = snapshotRepository.findByBranchId(branchOpt.get().getId());
            for (AnalyzedFileSnapshot s : direct) {
                snapshotsByPath.put(s.getFilePath(), s);
            }
        }

        // 2. Legacy analysis-level snapshots (fill gaps for files not yet in branch FK)
        List<AnalyzedFileSnapshot> legacy = snapshotRepository.findLatestSnapshotsByBranch(projectId, branchName);
        for (AnalyzedFileSnapshot s : legacy) {
            snapshotsByPath.putIfAbsent(s.getFilePath(), s);
        }

        return new ArrayList<>(snapshotsByPath.values());
    }

    /**
     * Get the file content for a specific file on a branch. Tries the direct branch_id FK
     * first; falls back to the legacy DISTINCT ON approach.
     */
    public Optional<String> getFileContentForBranch(Long projectId, String branchName, String filePath) {
        // Try direct FK first
        Optional<Branch> branchOpt = branchRepository.findByProjectIdAndBranchName(projectId, branchName);
        if (branchOpt.isPresent()) {
            Optional<String> direct = snapshotRepository
                    .findByBranchIdAndFilePathWithContent(branchOpt.get().getId(), filePath)
                    .map(s -> s.getFileContent().getContent());
            if (direct.isPresent()) {
                return direct;
            }
        }
        // Legacy fallback
        return snapshotRepository.findLatestSnapshotByBranchAndFilePath(projectId, branchName, filePath)
                .map(snapshot -> {
                    AnalyzedFileContent content = snapshot.getFileContent();
                    if (content != null) {
                        return content.getContent();
                    }
                    return null;
                });
    }

    // ── Source availability ──────────────────────────────────────────────

    /**
     * Get all branch names that have stored file snapshots.
     */
    public List<String> getBranchesWithSnapshots(Long projectId) {
        return snapshotRepository.findBranchNamesWithSnapshots(projectId);
    }

    /**
     * Get all PR numbers that have stored file snapshots.
     */
    public List<Long> getPrNumbersWithSnapshots(Long projectId) {
        return snapshotRepository.findPrNumbersWithSnapshots(projectId);
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
