package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.TaskImplementationEvidence;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.TaskImplementationEvidenceRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Owns validation, persistence, retrieval, and cache-copy behavior for bounded
 * deterministic task implementation evidence.
 */
@Service
public class TaskImplementationEvidenceService {

    public static final String SOURCE_DETERMINISTIC_PR_LEDGER = "DETERMINISTIC_PR_LEDGER";

    private static final Logger log =
            LoggerFactory.getLogger(TaskImplementationEvidenceService.class);
    private static final int MAX_ITEMS = 8;
    private static final int MAX_TOTAL_EXCERPT_CHARS = 2_400;
    private static final int MAX_EXCERPT_CHARS = 420;
    private static final int MAX_FILE_PATH_CHARS = 2_048;
    private static final int MAX_HUNK_ID_CHARS = 160;
    private static final int MAX_EVIDENCE_REF_CHARS = 32;

    private final TaskImplementationEvidenceRepository repository;

    public TaskImplementationEvidenceService(
            TaskImplementationEvidenceRepository repository) {
        this.repository = repository;
    }

    /**
     * Validate and persist the structured {@code taskEvidence} analysis output.
     * Existing fingerprints make webhook retries idempotent.
     */
    @Transactional
    public PersistenceResult persistFromAnalysisResponse(
            CodeAnalysis analysis,
            Object rawPayload) {
        if (analysis == null || analysis.getId() == null || rawPayload == null) {
            return PersistenceResult.empty();
        }
        if (analysis.getTaskId() == null || analysis.getTaskId().isBlank()
                || analysis.getPrNumber() == null
                || analysis.getCommitHash() == null
                || analysis.getCommitHash().isBlank()) {
            return PersistenceResult.empty();
        }
        if (!(rawPayload instanceof Map<?, ?> payload)) {
            return new PersistenceResult(0, 1, 0);
        }

        String taskKey = normalizedString(payload.get("taskKey"), 128);
        String source = normalizedString(payload.get("source"), 40);
        if (!analysis.getTaskId().equals(taskKey)
                || !SOURCE_DETERMINISTIC_PR_LEDGER.equals(source)) {
            log.warn(
                    "Skipping task evidence for analysis {}: task/source mismatch",
                    analysis.getId());
            return new PersistenceResult(0, 1, 0);
        }

        Object rawItems = payload.get("items");
        if (!(rawItems instanceof Collection<?> items) || items.isEmpty()) {
            return PersistenceResult.empty();
        }

        boolean fullEvidenceComplete = booleanValue(payload.get("fullEvidenceComplete"));
        Set<String> existingFingerprints = new HashSet<>(
                repository.findFingerprintsByAnalysisId(analysis.getId()));
        List<TaskImplementationEvidence> accepted = new ArrayList<>();
        int rejected = 0;
        int duplicate = 0;
        int usedExcerptChars = 0;

        for (Object rawItem : items) {
            if (accepted.size() >= MAX_ITEMS) {
                rejected++;
                continue;
            }
            if (!(rawItem instanceof Map<?, ?> item)) {
                rejected++;
                continue;
            }

            String evidenceRef = normalizedString(
                    item.get("evidenceRef"), MAX_EVIDENCE_REF_CHARS);
            String filePath = normalizedPath(item.get("filePath"));
            String hunkId = normalizedString(item.get("hunkId"), MAX_HUNK_ID_CHARS);
            Integer lineStart = positiveInteger(item.get("lineStart"));
            Integer lineEnd = positiveInteger(item.get("lineEnd"));
            String excerpt = normalizedExcerpt(item.get("excerpt"));

            if (evidenceRef == null || filePath == null || hunkId == null
                    || lineStart == null || lineEnd == null
                    || lineEnd < lineStart || excerpt == null
                    || usedExcerptChars + excerpt.length() > MAX_TOTAL_EXCERPT_CHARS) {
                rejected++;
                continue;
            }

            String fingerprint = fingerprint(
                    source, evidenceRef, filePath, hunkId,
                    lineStart, lineEnd, excerpt);
            if (!existingFingerprints.add(fingerprint)) {
                duplicate++;
                continue;
            }

            TaskImplementationEvidence evidence = new TaskImplementationEvidence();
            evidence.setAnalysis(analysis);
            evidence.setProject(analysis.getProject());
            evidence.setTaskId(taskKey);
            evidence.setPrNumber(analysis.getPrNumber());
            evidence.setCommitHash(analysis.getCommitHash());
            evidence.setSource(source);
            evidence.setEvidenceRef(evidenceRef);
            evidence.setFilePath(filePath);
            evidence.setHunkId(hunkId);
            evidence.setLineStart(lineStart);
            evidence.setLineEnd(lineEnd);
            evidence.setExcerpt(excerpt);
            evidence.setFullEvidenceComplete(fullEvidenceComplete);
            evidence.setContentFingerprint(fingerprint);
            accepted.add(evidence);
            usedExcerptChars += excerpt.length();
        }

        if (!accepted.isEmpty()) {
            repository.saveAll(accepted);
        }
        return new PersistenceResult(accepted.size(), rejected, duplicate);
    }

    /**
     * Load evidence for a bounded set of analyses. Failure is deliberately
     * observable and fail-open because this is optional prompt enrichment.
     */
    @Transactional(readOnly = true)
    public List<TaskImplementationEvidence> findForAnalyses(
            Collection<Long> analysisIds) {
        if (analysisIds == null || analysisIds.isEmpty()) {
            return List.of();
        }
        List<Long> distinctIds = analysisIds.stream()
                .filter(id -> id != null)
                .distinct()
                .toList();
        if (distinctIds.isEmpty()) {
            return List.of();
        }
        try {
            return repository.findByAnalysisIds(distinctIds);
        } catch (RuntimeException e) {
            log.warn("Task evidence lookup failed; continuing without persisted evidence: {}",
                    e.getMessage());
            return List.of();
        }
    }

    /**
     * Load the newest distinct evidence receipts across prior PR analyses for a
     * task. Evidence remains available when the latest analysis iteration could
     * not rebuild optional full-PR enrichment.
     */
    @Transactional(readOnly = true)
    public List<TaskImplementationEvidence> findForTaskHistory(
            Long projectId,
            String taskId,
            Long excludedPrNumber,
            int maxRecords) {
        if (projectId == null || taskId == null || taskId.isBlank()
                || maxRecords <= 0) {
            return List.of();
        }
        int boundedMax = Math.min(maxRecords, 40);
        try {
            List<TaskImplementationEvidence> candidates =
                    repository.findForTaskHistory(
                            projectId,
                            taskId,
                            excludedPrNumber,
                            PageRequest.of(0, Math.min(160, boundedMax * 4)));
            Map<String, TaskImplementationEvidence> distinct =
                    new LinkedHashMap<>();
            for (TaskImplementationEvidence evidence : candidates) {
                String key = evidence.getPrNumber()
                        + ":" + evidence.getContentFingerprint();
                distinct.putIfAbsent(key, evidence);
                if (distinct.size() >= boundedMax) {
                    break;
                }
            }
            return List.copyOf(distinct.values());
        } catch (RuntimeException e) {
            log.warn(
                    "Task evidence history lookup failed for project {} task {}; "
                            + "continuing without persisted evidence: {}",
                    projectId,
                    taskId,
                    e.getMessage());
            return List.of();
        }
    }

    /**
     * Copy immutable evidence when a cached analysis is cloned for another PR.
     */
    @Transactional
    public PersistenceResult copyForAnalysis(
            CodeAnalysis sourceAnalysis,
            CodeAnalysis targetAnalysis) {
        if (sourceAnalysis == null || targetAnalysis == null
                || sourceAnalysis.getId() == null || targetAnalysis.getId() == null
                || targetAnalysis.getTaskId() == null
                || targetAnalysis.getTaskId().isBlank()) {
            return PersistenceResult.empty();
        }
        List<TaskImplementationEvidence> sourceEvidence =
                repository.findByAnalysisIds(List.of(sourceAnalysis.getId()));
        if (sourceEvidence.isEmpty()) {
            return PersistenceResult.empty();
        }

        Map<String, Object> payload = Map.of(
                "taskKey", targetAnalysis.getTaskId(),
                "source", SOURCE_DETERMINISTIC_PR_LEDGER,
                "fullEvidenceComplete", sourceEvidence.stream()
                        .allMatch(TaskImplementationEvidence::isFullEvidenceComplete),
                "items", sourceEvidence.stream().map(evidence -> Map.of(
                        "evidenceRef", evidence.getEvidenceRef(),
                        "filePath", evidence.getFilePath(),
                        "hunkId", evidence.getHunkId(),
                        "lineStart", evidence.getLineStart(),
                        "lineEnd", evidence.getLineEnd(),
                        "excerpt", evidence.getExcerpt()
                )).toList()
        );
        return persistFromAnalysisResponse(targetAnalysis, payload);
    }

    private String normalizedPath(Object value) {
        String path = normalizedString(value, MAX_FILE_PATH_CHARS);
        if (path == null) {
            return null;
        }
        return path.replace('\\', '/');
    }

    private String normalizedExcerpt(Object value) {
        String excerpt = normalizedString(value, MAX_EXCERPT_CHARS);
        if (excerpt == null) {
            return null;
        }
        return excerpt
                .replace('\u0000', ' ')
                .replaceAll("[\\t\\x0B\\f\\r ]+", " ")
                .trim();
    }

    private String normalizedString(Object value, int maxChars) {
        if (!(value instanceof String text)) {
            return null;
        }
        String normalized = text.replace('\u0000', ' ').trim();
        if (normalized.isEmpty() || normalized.length() > maxChars) {
            return null;
        }
        return normalized;
    }

    private Integer positiveInteger(Object value) {
        if (value instanceof Number number) {
            try {
                int result = new BigDecimal(number.toString()).intValueExact();
                return result > 0 ? result : null;
            } catch (ArithmeticException | NumberFormatException ignored) {
                return null;
            }
        }
        return null;
    }

    private boolean booleanValue(Object value) {
        return value instanceof Boolean bool && bool;
    }

    private String fingerprint(
            String source,
            String evidenceRef,
            String filePath,
            String hunkId,
            int lineStart,
            int lineEnd,
            String excerpt) {
        String canonical = String.join("\n",
                source.toUpperCase(Locale.ROOT),
                evidenceRef,
                filePath,
                hunkId,
                Integer.toString(lineStart),
                Integer.toString(lineEnd),
                excerpt);
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(canonical.getBytes(StandardCharsets.UTF_8));
            return java.util.HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is not available", e);
        }
    }

    public record PersistenceResult(int persisted, int rejected, int duplicate) {
        public static PersistenceResult empty() {
            return new PersistenceResult(0, 0, 0);
        }
    }
}
