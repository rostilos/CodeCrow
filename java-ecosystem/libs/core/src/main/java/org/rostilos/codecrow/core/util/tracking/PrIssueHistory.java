package org.rostilos.codecrow.core.util.tracking;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import java.time.OffsetDateTime;
import java.util.*;

/** Explicit user decisions remain authoritative across PR iterations and line shifts. */
public final class PrIssueHistory {
    private PrIssueHistory() {}

    public static String identity(CodeAnalysisIssue issue) {
        String fingerprint = issue.getContentFingerprint() != null
                ? issue.getContentFingerprint() : issue.getIssueFingerprint();
        String anchor = AnchoredIssueIdentity.forFingerprint(issue, fingerprint);
        String path = AnchoredIssueIdentity.normalizeRepositoryPath(issue.getFilePath());
        if (path == null) return null;
        String prefix = path + "\0" + issue.getAnchorSide() + "\0";
        if (anchor != null) return prefix + anchor;
        String title = normalize(issue.getTitle()), reason = normalize(issue.getReason());
        // Match complete narratives, never a short generic title without source evidence.
        return !title.isBlank() && reason.length() >= 40 ? prefix + title + "\0" + reason : null;
    }

    private static String normalize(String value) {
        return value == null ? "" : value.strip().replaceAll("\\s+", " ").toLowerCase(Locale.ROOT);
    }

    public static Boolean manualStatus(CodeAnalysisIssue issue) {
        if (issue.getManualStatusResolved() != null) return issue.getManualStatusResolved();
        return issue.isResolved() && "manual".equals(issue.getResolvedBy()) ? Boolean.TRUE : null;
    }

    public static OffsetDateTime decisionTime(CodeAnalysisIssue issue) {
        if (issue.getManualStatusUpdatedAt() != null) return issue.getManualStatusUpdatedAt();
        if (issue.getResolvedAt() != null) return issue.getResolvedAt();
        return issue.getCreatedAt() != null ? issue.getCreatedAt() : OffsetDateTime.MIN;
    }

    public static Map<String, CodeAnalysisIssue> decisions(Collection<CodeAnalysisIssue> history) {
        Map<String, CodeAnalysisIssue> result = new HashMap<>();
        for (CodeAnalysisIssue issue : history) {
            if (manualStatus(issue) == null) continue;
            for (String key : keys(issue)) result.merge(key, issue, PrIssueHistory::latest);
        }
        return result;
    }

    private static CodeAnalysisIssue latest(CodeAnalysisIssue first, CodeAnalysisIssue second) {
        return decisionTime(second).isAfter(decisionTime(first)) ? second : first;
    }

    private static List<String> keys(CodeAnalysisIssue issue) {
        List<String> keys = new ArrayList<>();
        String identity = identity(issue);
        if (identity != null) keys.add(identity);
        if (issue.getId() != null) keys.add("id:" + issue.getId());
        if (issue.getTrackedFromIssueId() != null) keys.add("id:" + issue.getTrackedFromIssueId());
        return keys;
    }

    public static CodeAnalysisIssue decisionFor(CodeAnalysisIssue issue, Map<String, CodeAnalysisIssue> decisions) {
        CodeAnalysisIssue selected = null;
        for (String key : keys(issue)) {
            CodeAnalysisIssue candidate = decisions.get(key);
            if (candidate != null) selected = selected == null ? candidate : latest(selected, candidate);
        }
        return selected;
    }

    public static boolean effectiveResolved(CodeAnalysisIssue issue, CodeAnalysisIssue decision) {
        if (decision == null) return issue.isResolved();
        if (Boolean.TRUE.equals(manualStatus(decision))) return true;
        // Reopening removes earlier dismissals; a later source-backed resolution can still close it.
        return issue.isResolved() && issue.getResolvedAt() != null
                && issue.getResolvedAt().isAfter(decisionTime(decision));
    }

    public static void apply(CodeAnalysisIssue issue, CodeAnalysisIssue decision) {
        if (decision == null) return;
        boolean resolved = effectiveResolved(issue, decision);
        issue.setManualStatusResolved(manualStatus(decision));
        issue.setManualStatusUpdatedAt(decisionTime(decision));
        issue.setResolved(resolved);
        if (Boolean.TRUE.equals(manualStatus(decision))) {
            issue.setResolvedDescription(decision.getResolvedDescription());
            issue.setResolvedBy(decision.getResolvedBy());
            issue.setResolvedAt(decision.getResolvedAt());
            issue.setResolvedByPr(decision.getResolvedByPr());
            issue.setResolvedCommitHash(decision.getResolvedCommitHash());
            issue.setResolvedAnalysisId(decision.getResolvedAnalysisId());
        } else if (!resolved) {
            issue.setResolvedDescription(null); issue.setResolvedBy(null); issue.setResolvedAt(null);
            issue.setResolvedByPr(null); issue.setResolvedCommitHash(null); issue.setResolvedAnalysisId(null);
        }
    }
}
