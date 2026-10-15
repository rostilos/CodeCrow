package org.rostilos.codecrow.core.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.PrChangeSummary;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.PrChangeSummaryRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.List;
import java.util.Objects;

/** Optional enrichment: persistence/lookup failures never prevent core analysis. */
@Service
public class PrChangeSummaryService {
    private static final Logger log = LoggerFactory.getLogger(PrChangeSummaryService.class);
    private final PrChangeSummaryRepository repository;
    private final ObjectMapper mapper;

    public PrChangeSummaryService(PrChangeSummaryRepository repository, ObjectMapper mapper) {
        this.repository = repository;
        this.mapper = mapper;
    }

    public void persist(CodeAnalysis analysis, Object raw) {
        if (raw == null || analysis == null || analysis.getId() == null) return;
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
            repository.save(new PrChangeSummary(analysis, mapper.writeValueAsString(raw)));
            log.info("PR change summary persisted for analysis {} project {} PR {}", analysis.getId(),
                    analysis.getProject().getId(), analysis.getPrNumber());
        } catch (Exception exception) {
            log.warn("PR change summary persistence unavailable for analysis {}; review continues: {}",
                    analysis.getId(), exception.getClass().getSimpleName());
        }
    }

    public Map<String, Object> find(Long projectId, Long prNumber, String sourceRevision) {
        if (projectId == null || prNumber == null || sourceRevision == null || sourceRevision.isBlank()) return null;
        try {
            var found = repository.findFirstByProjectIdAndPrNumberAndSourceRevisionOrderByCreatedAtDesc(
                    projectId, prNumber, sourceRevision);
            if (found.isEmpty()) return null;
            Map<String, Object> payload = mapper.readValue(found.get().getPayload(), new TypeReference<>() {});
            if (!Objects.equals(String.valueOf(projectId), String.valueOf(payload.get("projectId")))
                    || !Objects.equals(String.valueOf(prNumber), String.valueOf(payload.get("pullRequestId")))
                    || !sourceRevision.equals(payload.get("sourceRevision"))
                    || !(payload.get("files") instanceof List<?> files) || files.isEmpty()
                    || files.stream().anyMatch(file -> !(file instanceof Map<?, ?> node)
                        || !(node.get("path") instanceof String))) {
                log.warn("Saved PR change summary malformed for project {} PR {}; using source fallback", projectId, prNumber);
                return null;
            }
            return payload;
        } catch (Exception exception) {
            log.warn("PR change summary lookup unavailable for project {} PR {}; using source fallback: {}",
                    projectId, prNumber, exception.getClass().getSimpleName());
            return null;
        }
    }

    public void copyForAnalysis(CodeAnalysis source, CodeAnalysis target) {
        if (source == null || target == null || source.getProject() == null || target.getProject() == null
                || !Objects.equals(source.getProject().getId(), target.getProject().getId())
                || !Objects.equals(source.getPrNumber(), target.getPrNumber())
                || !Objects.equals(source.getCommitHash(), target.getCommitHash())) return;
        persist(target, find(source.getProject().getId(), source.getPrNumber(), source.getCommitHash()));
    }
}
