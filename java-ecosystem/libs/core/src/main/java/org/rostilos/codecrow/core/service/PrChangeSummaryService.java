package org.rostilos.codecrow.core.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.List;
import java.util.Objects;

/** Optional context stored in the owning analysis transaction; malformed context falls back to source. */
@Service
public class PrChangeSummaryService {
    private static final Logger log = LoggerFactory.getLogger(PrChangeSummaryService.class);
    private final CodeAnalysisRepository repository;
    private final ObjectMapper mapper;

    public PrChangeSummaryService(CodeAnalysisRepository repository, ObjectMapper mapper) {
        this.repository = repository;
        this.mapper = mapper;
    }

    public void attach(CodeAnalysis analysis, Object raw) {
        if (raw == null || analysis == null) return;
        try {
            if (!(raw instanceof Map<?, ?> payload)
                    || analysis.getProject() == null || analysis.getPrNumber() == null
                    || analysis.getCommitHash() == null || analysis.getCommitHash().isBlank()
                    || !Objects.equals(String.valueOf(analysis.getProject().getId()), String.valueOf(payload.get("projectId")))
                    || !Objects.equals(String.valueOf(analysis.getPrNumber()), String.valueOf(payload.get("pullRequestId")))
                    || !analysis.getCommitHash().equals(payload.get("sourceRevision"))) {
                log.warn("PR change summary identity mismatch for analysis {}; enrichment skipped", analysis.getId());
                return;
            }
            analysis.setPrChangeSummaryPayload(mapper.writeValueAsString(raw));
            log.info("PR change summary attached to analysis {} project {} PR {}", analysis.getId(),
                    analysis.getProject().getId(), analysis.getPrNumber());
        } catch (Exception exception) {
            log.warn("PR change summary serialization unavailable for analysis {}; review continues: {}",
                    analysis.getId(), exception.getClass().getSimpleName());
        }
    }

    public Map<String, Object> find(Long projectId, Long prNumber, String sourceRevision, Long analysisId) {
        if (projectId == null || prNumber == null || sourceRevision == null || sourceRevision.isBlank()) return null;
        try {
            var found = analysisId == null
                    ? repository.findByProjectIdAndCommitHashAndPrNumber(projectId, sourceRevision, prNumber)
                    : repository.findByIdAndProjectId(analysisId, projectId);
            if (found.isEmpty() || !Objects.equals(prNumber, found.get().getPrNumber())
                    || !sourceRevision.equals(found.get().getCommitHash())
                    || found.get().getPrChangeSummaryPayload() == null) return null;
            Map<String, Object> payload = mapper.readValue(found.get().getPrChangeSummaryPayload(), new TypeReference<>() {});
            if (!Objects.equals(String.valueOf(projectId), String.valueOf(payload.get("projectId")))
                    || !Objects.equals(String.valueOf(prNumber), String.valueOf(payload.get("pullRequestId")))
                    || !sourceRevision.equals(payload.get("sourceRevision"))
                    || !"complete".equals(payload.getOrDefault("semanticStatus", "complete"))
                    || !(payload.get("files") instanceof List<?> files) || files.isEmpty()
                    || files.stream().anyMatch(file -> !(file instanceof Map<?, ?> node)
                        || !(node.get("path") instanceof String)
                        || (node.containsKey("scanStatus") && !"scanned".equals(node.get("scanStatus"))))) {
                log.warn("Saved PR change summary incomplete or malformed for project {} PR {}; using source fallback", projectId, prNumber);
                return null;
            }
            return payload;
        } catch (Exception exception) {
            log.warn("PR change summary lookup unavailable for project {} PR {}; using source fallback: {}",
                    projectId, prNumber, exception.getClass().getSimpleName());
            return null;
        }
    }

    /** Host-acquired source remains usable when the optional semantic scan was unavailable. */
    public Map<String, Object> reviewSource(CodeAnalysis analysis) {
        if (analysis == null || analysis.getPrChangeSummaryPayload() == null) return Map.of();
        try {
            var root = mapper.readTree(analysis.getPrChangeSummaryPayload());
            if (!Objects.equals(root.path("sourceRevision").asText(), analysis.getCommitHash())
                    || root.path("projectId").asLong(-1) != analysis.getProject().getId()
                    || root.path("pullRequestId").asLong(-1) != analysis.getPrNumber()) return Map.of();
            var source = root.path("toolContext").path("reviewSource");
            return source.isObject() ? mapper.convertValue(source, new TypeReference<>() {}) : Map.of();
        } catch (Exception unavailable) {
            log.warn("Saved review source unavailable for analysis {}; using pinned provider fallback", analysis.getId());
            return Map.of();
        }
    }

    public void copyForAnalysis(CodeAnalysis source, CodeAnalysis target) {
        if (source == null || target == null || source.getProject() == null || target.getProject() == null
                || !Objects.equals(source.getProject().getId(), target.getProject().getId())
                || !Objects.equals(source.getPrNumber(), target.getPrNumber())
                || !Objects.equals(source.getCommitHash(), target.getCommitHash())
                || source.getDiffFingerprint() == null
                || !source.getDiffFingerprint().equals(target.getDiffFingerprint())) return;
        target.setPrChangeSummaryPayload(source.getPrChangeSummaryPayload());
    }
}
