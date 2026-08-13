package org.rostilos.codecrow.core.util.tracking;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/** Projects valid active lineage tips from all persisted issue occurrences. */
public final class PrIssueLineage {

    private PrIssueLineage() {
    }

    public static Projection project(List<CodeAnalysisIssue> occurrences) {
        if (occurrences == null || occurrences.isEmpty()) {
            return new Projection(List.of(), List.of());
        }

        Map<Long, CodeAnalysisIssue> byId = new HashMap<>();
        for (CodeAnalysisIssue issue : occurrences) {
            if (issue != null && issue.getId() != null) {
                byId.put(issue.getId(), issue);
            }
        }

        Set<Long> supersededIds = new HashSet<>();
        List<InvalidEdge> invalidEdges = new ArrayList<>();
        for (CodeAnalysisIssue child : occurrences) {
            if (child == null || child.getTrackedFromIssueId() == null) {
                continue;
            }
            CodeAnalysisIssue predecessor = byId.get(child.getTrackedFromIssueId());
            String invalidReason = invalidReason(child, predecessor);
            if (invalidReason == null) {
                supersededIds.add(predecessor.getId());
            } else {
                invalidEdges.add(new InvalidEdge(
                        child.getId(), child.getTrackedFromIssueId(), invalidReason));
            }
        }

        List<CodeAnalysisIssue> activeTips = occurrences.stream()
                .filter(Objects::nonNull)
                .filter(issue -> issue.getId() == null || !supersededIds.contains(issue.getId()))
                .filter(issue -> !issue.isResolved())
                .toList();
        return new Projection(activeTips, invalidEdges);
    }

    /**
     * A stored edge is authoritative only inside one project/repository/PR and
     * only when it points backward to an unresolved occurrence.
     */
    public static String invalidReason(CodeAnalysisIssue child, CodeAnalysisIssue predecessor) {
        if (predecessor == null) {
            return "dangling-or-cross-scope predecessor";
        }
        if (child == predecessor || Objects.equals(child.getId(), predecessor.getId())) {
            return "self-cycle";
        }
        CodeAnalysis childAnalysis = child.getAnalysis();
        CodeAnalysis predecessorAnalysis = predecessor.getAnalysis();
        if (childAnalysis == null || predecessorAnalysis == null
                || childAnalysis.getProject() == null || predecessorAnalysis.getProject() == null
                || childAnalysis.getProject().getId() == null
                || predecessorAnalysis.getProject().getId() == null
                || !childAnalysis.getProject().getId().equals(predecessorAnalysis.getProject().getId())
                || childAnalysis.getPrNumber() == null
                || !childAnalysis.getPrNumber().equals(predecessorAnalysis.getPrNumber())) {
            return "cross-project-or-PR predecessor";
        }
        if (predecessor.isResolved()) {
            return "closed predecessor";
        }
        if (!isOlder(predecessorAnalysis, childAnalysis)) {
            return "non-older predecessor";
        }
        return null;
    }

    private static boolean isOlder(CodeAnalysis predecessor, CodeAnalysis child) {
        Integer predecessorVersion = predecessor.getPrVersion();
        Integer childVersion = child.getPrVersion();
        if (predecessorVersion != null && childVersion != null
                && !predecessorVersion.equals(childVersion)) {
            return predecessorVersion < childVersion;
        }
        if (predecessor.getCreatedAt() != null && child.getCreatedAt() != null
                && !predecessor.getCreatedAt().equals(child.getCreatedAt())) {
            return predecessor.getCreatedAt().isBefore(child.getCreatedAt());
        }
        return predecessor.getId() != null && child.getId() != null
                && predecessor.getId() < child.getId();
    }

    public record Projection(
            List<CodeAnalysisIssue> activeTips,
            List<InvalidEdge> invalidEdges
    ) {
    }

    public record InvalidEdge(Long childId, Long predecessorId, String reason) {
    }
}
