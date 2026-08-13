package org.rostilos.codecrow.analysisengine.service.pr;

import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.IssueReconciliationEngine;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.util.tracking.PrIssueLineage;
import org.rostilos.codecrow.core.util.tracking.PrIssueLineageFingerprint;
import org.rostilos.codecrow.core.util.tracking.TrackingConfidence;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * Links freshly verified PR findings to active occurrences from every earlier run.
 *
 * <p>Historical findings are never copied into the current analysis and omission
 * never resolves them. Each current finding and historical tip participates in at
 * most one exact match. Category and severity are not identity inputs.</p>
 */
@Service
@Transactional
public class PrIssueTrackingService {

    private static final Logger log = LoggerFactory.getLogger(PrIssueTrackingService.class);

    private final CodeAnalysisIssueRepository issueRepository;

    /** Kept in the constructor to preserve the existing module wiring. */
    public PrIssueTrackingService(
            CodeAnalysisIssueRepository issueRepository,
            IssueReconciliationEngine ignoredReconciliationEngine,
            AstScopeEnricher ignoredAstScopeEnricher
    ) {
        this.issueRepository = issueRepository;
    }

    /**
     * Reconcile {@code newAnalysis} with all earlier occurrences from its PR.
     * File-content arguments remain for source compatibility; lineage matching
     * uses the already persisted current-head anchor receipt.
     */
    public TrackingSummary trackPrIteration(
            CodeAnalysis newAnalysis,
            CodeAnalysis previousAnalysis,
            Map<String, String> newFileContents,
            Map<String, String> prevFileContents
    ) {
        if (newAnalysis == null || newAnalysis.getProject() == null
                || newAnalysis.getProject().getId() == null
                || newAnalysis.getPrNumber() == null) {
            log.warn("Skipping PR lineage reconciliation for an unscoped analysis");
            return new TrackingSummary(0, 0, issueCount(newAnalysis), 0);
        }
        List<CodeAnalysisIssue> scopedOccurrences = issueRepository.findByProjectIdAndPrNumber(
                newAnalysis.getProject().getId(), newAnalysis.getPrNumber());
        return trackAgainstScopedHistory(newAnalysis, scopedOccurrences);
    }

    /**
     * Package-visible for focused tests and cache-path reconciliation.
     */
    TrackingSummary trackAgainstScopedHistory(
            CodeAnalysis newAnalysis,
            List<CodeAnalysisIssue> scopedOccurrences
    ) {
        List<CodeAnalysisIssue> currentIssues = newAnalysis.getIssues() != null
                ? new ArrayList<>(newAnalysis.getIssues())
                : List.of();
        Set<Long> currentIds = currentIssues.stream()
                .map(CodeAnalysisIssue::getId)
                .filter(Objects::nonNull)
                .collect(java.util.stream.Collectors.toSet());

        List<CodeAnalysisIssue> historicalOccurrences = scopedOccurrences == null
                ? new ArrayList<>()
                : scopedOccurrences.stream()
                        .filter(Objects::nonNull)
                        .filter(issue -> issue.getAnalysis() != newAnalysis)
                        .filter(issue -> issue.getAnalysis() == null
                                || !Objects.equals(issue.getAnalysis().getId(), newAnalysis.getId()))
                        .filter(issue -> issue.getId() == null || !currentIds.contains(issue.getId()))
                        .toList();

        PrIssueLineage.Projection projection = PrIssueLineage.project(historicalOccurrences);
        for (PrIssueLineage.InvalidEdge invalidEdge : projection.invalidEdges()) {
            log.warn("Ignoring invalid stored PR lineage edge child={} predecessor={}: {}",
                    invalidEdge.childId(), invalidEdge.predecessorId(), invalidEdge.reason());
        }

        Map<String, List<CodeAnalysisIssue>> tipsByFingerprint = new LinkedHashMap<>();
        Map<String, List<CodeAnalysisIssue>> tipsByLegacyAnchor = new LinkedHashMap<>();
        for (CodeAnalysisIssue tip : projection.activeTips()) {
            String fingerprint = ensureLineageFingerprint(tip);
            if (fingerprint != null) {
                tipsByFingerprint.computeIfAbsent(fingerprint, ignored -> new ArrayList<>()).add(tip);
            }
            String legacyAnchor = legacyAnchorIdentity(tip);
            if (legacyAnchor != null) {
                tipsByLegacyAnchor.computeIfAbsent(legacyAnchor, ignored -> new ArrayList<>()).add(tip);
            }
        }
        tipsByFingerprint.values().forEach(tips -> tips.sort(
                Comparator.comparing(PrIssueTrackingService::analysisOrder).reversed()));

        Set<Long> consumedTipIds = new HashSet<>();
        int matched = 0;
        int newOnly = 0;
        for (CodeAnalysisIssue current : currentIssues) {
            // Discovery-supplied IDs are never trusted as links. Only this
            // scoped host matcher is allowed to set lineage on a fresh row.
            current.setTrackedFromIssueId(null);
            current.setTrackingConfidence(null);
            if (current.isResolved()) {
                newOnly++;
                continue;
            }

            String fingerprint = ensureLineageFingerprint(current);
            List<CodeAnalysisIssue> candidates = fingerprint != null
                    ? tipsByFingerprint.getOrDefault(fingerprint, List.of())
                    : List.of();
            CodeAnalysisIssue match = uniqueAvailableExactAnchor(
                    current, candidates, consumedTipIds);
            if (match == null) {
                String legacyAnchor = legacyAnchorIdentity(current);
                match = uniqueAvailableExactAnchor(
                        current,
                        legacyAnchor != null
                                ? tipsByLegacyAnchor.getOrDefault(legacyAnchor, List.of())
                                : List.of(),
                        consumedTipIds);
            }
            if (match == null) {
                newOnly++;
                continue;
            }

            current.setTrackedFromIssueId(match.getId());
            current.setTrackingConfidence(TrackingConfidence.EXACT);
            consumedTipIds.add(match.getId());
            issueRepository.save(current);
            matched++;
        }

        long previouslyResolved = historicalOccurrences.stream()
                .filter(CodeAnalysisIssue::isResolved)
                .count();
        log.info("PR all-run lineage for analysis {}: {} matched, {} new, {} historical active tips, "
                        + "{} invalid edges; omission resolved 0",
                newAnalysis.getId(), matched, newOnly, projection.activeTips().size(),
                projection.invalidEdges().size());
        return new TrackingSummary(matched, 0, newOnly, previouslyResolved);
    }

    /** Recompute cleared clone receipts before destination-PR matching. */
    private String ensureLineageFingerprint(CodeAnalysisIssue issue) {
        if (issue.getLineageFingerprint() == null || issue.getLineageFingerprint().isBlank()) {
            issue.setLineageFingerprint(PrIssueLineageFingerprint.computePersisted(issue));
            if (issue.getId() != null) {
                issueRepository.save(issue);
            }
        }
        return issue.getLineageFingerprint();
    }

    /**
     * Exact persisted anchors disambiguate a receipt collision (or a legacy
     * content-fingerprint match); ambiguity deliberately creates a new root.
     */
    private CodeAnalysisIssue uniqueAvailableExactAnchor(
            CodeAnalysisIssue current,
            List<CodeAnalysisIssue> candidates,
            Set<Long> consumedTipIds
    ) {
        List<CodeAnalysisIssue> available = candidates.stream()
                .filter(candidate -> candidate.getId() != null)
                .filter(candidate -> !consumedTipIds.contains(candidate.getId()))
                .filter(candidate -> exactAnchorMatches(current, candidate))
                .toList();
        return available.size() == 1 ? available.get(0) : null;
    }

    private boolean exactAnchorMatches(CodeAnalysisIssue left, CodeAnalysisIssue right) {
        if (!Objects.equals(normalizePath(left.getFilePath()), normalizePath(right.getFilePath()))) {
            return false;
        }
        if (left.getLineHash() != null && right.getLineHash() != null) {
            return left.getLineHash().equals(right.getLineHash());
        }
        if (left.getCodeSnippet() != null && !left.getCodeSnippet().isBlank()
                && right.getCodeSnippet() != null && !right.getCodeSnippet().isBlank()) {
            return normalizeSnippet(left.getCodeSnippet()).equals(normalizeSnippet(right.getCodeSnippet()));
        }
        return left.getIssueScope() != null
                && left.getIssueScope() == right.getIssueScope()
                && left.getIssueScope().name().equals("FILE");
    }

    /** Category-independent bridge for rows created before lineage receipts existed. */
    private String legacyAnchorIdentity(CodeAnalysisIssue issue) {
        if (issue.getContentFingerprint() == null || issue.getContentFingerprint().isBlank()) {
            return null;
        }
        return normalizePath(issue.getFilePath()) + "\n" + issue.getContentFingerprint();
    }

    private static String normalizePath(String path) {
        return path == null ? "" : path.strip().replace('\\', '/');
    }

    private static String normalizeSnippet(String snippet) {
        return snippet.strip().replaceAll("\\s+", " ");
    }

    private static long analysisOrder(CodeAnalysisIssue issue) {
        if (issue.getAnalysis() != null && issue.getAnalysis().getPrVersion() != null) {
            return issue.getAnalysis().getPrVersion();
        }
        return issue.getAnalysis() != null && issue.getAnalysis().getId() != null
                ? issue.getAnalysis().getId()
                : issue.getId() != null ? issue.getId() : 0L;
    }

    private static int issueCount(CodeAnalysis analysis) {
        return analysis != null && analysis.getIssues() != null ? analysis.getIssues().size() : 0;
    }

    public record TrackingSummary(
            int matchedCount,
            int resolvedCount,
            int newIssueCount,
            long previouslyResolvedCount,
            int unanchoredResolvedCount,
            int unanchoredPersistingCount
    ) {
        public TrackingSummary(
                int matchedCount,
                int resolvedCount,
                int newIssueCount,
                long previouslyResolvedCount
        ) {
            this(matchedCount, resolvedCount, newIssueCount, previouslyResolvedCount, 0, 0);
        }

        public boolean isFirstIteration() {
            return matchedCount == 0 && resolvedCount == 0 && previouslyResolvedCount == 0;
        }
    }
}
