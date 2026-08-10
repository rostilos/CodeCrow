package org.rostilos.codecrow.core.service;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.util.tracking.AnchoredIssueIdentity;
import org.rostilos.codecrow.core.util.tracking.DiffSanitizer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;

/**
 * De-duplicates issues at ingestion time (before persistence) using exact,
 * deterministic issue identities.
 * <p>
 * Location alone is deliberately not an identity. A single line or file can contain
 * multiple independent defects in the same category, so merging on
 * {@code file:line:category} or treating a file-scoped issue as a wildcard would
 * suppress valid findings.
 * <p>
 * <h3>De-duplication identities</h3>
 * <ol>
 *   <li><b>Exact narrative identity</b> — same normalized title and complete
 *       root-cause reason in the same file. Category, severity, and a stale line
 *       anchor do not make the same finding independent.</li>
 *   <li><b>Issue fingerprint</b> — exact match on
 *       {@link org.rostilos.codecrow.core.util.tracking.IssueFingerprint}
 *       (category + anchored line-content hash + normalized title).</li>
 *   <li><b>Content fingerprint</b> — exact match on the category-agnostic
 *       anchored-content fingerprint. This handles classification drift for an
 *       otherwise identical anchored title.</li>
 * </ol>
 * A fingerprint is mergeable only when it is backed by an actual line hash or
 * exact snippet anchor. Placeholder {@code no_hash} fingerprints therefore cannot
 * suppress findings after incomplete source acquisition. Issues without a
 * mergeable identity pass through unchanged. Resolved lifecycle records also pass
 * through unchanged.
 * <p>
 * When merging duplicates, the surviving issue keeps:
 * <ul>
 *   <li>The highest severity among the group</li>
 *   <li>The best (longest valid) suggested fix diff</li>
 *   <li>The strongest concrete source anchor</li>
 *   <li>Additional concrete locations for exact narrative occurrences</li>
 * </ul>
 *
 * <h3>Thread safety</h3>
 * Instances are stateless beyond the logger. Safe to call from any thread.
 */
@Service
public class IssueDeduplicationService {

    private static final Logger log = LoggerFactory.getLogger(IssueDeduplicationService.class);

    /**
     * Severity ordering for comparison — higher value wins when merging duplicates.
     */
    private static final Map<IssueSeverity, Integer> SEVERITY_ORDER = Map.of(
            IssueSeverity.HIGH, 4,
            IssueSeverity.MEDIUM, 3,
            IssueSeverity.LOW, 2,
            IssueSeverity.INFO, 1
    );

    /**
     * De-duplicate a list of issues in-place (well, returns a new list).
     * <p>
     * Issues are grouped by file path and then passed through the exact-identity tiers.
     * Resolved issues are never de-duplicated — they always pass through.
     *
     * @param issues the list of issues to de-duplicate; not modified
     * @return a new list with duplicates removed; order is preserved per file
     */
    public List<CodeAnalysisIssue> deduplicateAtIngestion(List<CodeAnalysisIssue> issues) {
        if (issues == null || issues.size() < 2) {
            return issues != null ? new ArrayList<>(issues) : new ArrayList<>();
        }

        // Partition by file path
        Map<String, List<CodeAnalysisIssue>> byFile = new LinkedHashMap<>();
        for (CodeAnalysisIssue issue : issues) {
            String path = issue.getFilePath() != null ? issue.getFilePath() : "unknown";
            byFile.computeIfAbsent(path, k -> new ArrayList<>()).add(issue);
        }

        List<CodeAnalysisIssue> result = new ArrayList<>(issues.size());
        int totalRemoved = 0;

        for (Map.Entry<String, List<CodeAnalysisIssue>> entry : byFile.entrySet()) {
            String filePath = entry.getKey();
            List<CodeAnalysisIssue> fileIssues = entry.getValue();

            if (fileIssues.size() < 2) {
                result.addAll(fileIssues);
                continue;
            }

            // Separate resolved issues — never dedup those
            List<CodeAnalysisIssue> active = new ArrayList<>();
            List<CodeAnalysisIssue> resolved = new ArrayList<>();
            for (CodeAnalysisIssue issue : fileIssues) {
                if (issue.isResolved()) {
                    resolved.add(issue);
                } else {
                    active.add(issue);
                }
            }

            int before = active.size();

            // Exact root narrative catches duplicated history/current output even
            // when line anchors and classifications drifted before ingestion.
            active = exactNarrativeDedup(active, filePath);

            // Exact category-aware identity.
            active = fingerprintDedup(active, filePath);

            // Exact category-agnostic identity for classification drift.
            active = contentFingerprintDedup(active, filePath);

            int removed = before - active.size();
            totalRemoved += removed;

            if (removed > 0) {
                log.info("Dedup removed {} duplicate(s) in {} (kept {})", removed, filePath, active.size());
            }

            result.addAll(active);
            result.addAll(resolved);
        }

        if (totalRemoved > 0) {
            log.info("Ingestion dedup: removed {} total duplicates across {} files ({} → {} issues)",
                    totalRemoved, byFile.size(), issues.size(), result.size());
        }

        return result;
    }

    // ── Tier 1: complete root-narrative identity ─────────────────────────

    private List<CodeAnalysisIssue> exactNarrativeDedup(
            List<CodeAnalysisIssue> issues,
            String filePath
    ) {
        if (issues.size() < 2) {
            return issues;
        }

        Map<String, CodeAnalysisIssue> byNarrative = new LinkedHashMap<>();
        int noIdentityOrdinal = 0;
        int duplicateCount = 0;
        for (CodeAnalysisIssue issue : issues) {
            String title = normalizeNarrative(issue.getTitle());
            String reason = normalizeNarrative(rootReason(issue.getReason()));
            // Short boilerplate (for example "same issue" / "test reason") is
            // not a safe root identity. The inference tier handles ambiguous
            // prose; this ingestion fallback requires a complete explanation.
            if (title.isBlank() || reason.length() < 40) {
                byNarrative.put("__no_narrative_" + noIdentityOrdinal++, issue);
                continue;
            }

            String identity = title + "\u0000" + reason;
            CodeAnalysisIssue existing = byNarrative.get(identity);
            if (existing == null) {
                byNarrative.put(identity, issue);
                continue;
            }

            CodeAnalysisIssue winner = pickBest(existing, issue);
            preserveAdditionalNarrativeLocation(winner, existing, issue);
            byNarrative.put(identity, winner);
            duplicateCount++;
        }

        if (duplicateCount > 0) {
            log.debug("Exact-narrative dedup: removed {} in {}", duplicateCount, filePath);
        }
        return new ArrayList<>(byNarrative.values());
    }

    private String normalizeNarrative(String value) {
        if (value == null) {
            return "";
        }
        return value.strip().replaceAll("\\s+", " ").toLowerCase(Locale.ROOT);
    }

    private String rootReason(String reason) {
        if (reason == null || reason.isBlank()) {
            return "";
        }
        return Arrays.stream(reason.split("\\R"))
                .filter(line -> !line.strip().toLowerCase(Locale.ROOT).startsWith("also affects:"))
                .reduce((left, right) -> left + "\n" + right)
                .orElse("")
                .strip();
    }

    private void preserveAdditionalNarrativeLocation(
            CodeAnalysisIssue winner,
            CodeAnalysisIssue first,
            CodeAnalysisIssue second
    ) {
        Set<String> locations = new TreeSet<>();
        collectGeneratedLocations(first.getReason(), locations);
        collectGeneratedLocations(second.getReason(), locations);
        addConcreteSecondaryLocation(winner, first, locations);
        addConcreteSecondaryLocation(winner, second, locations);

        String primary = issueLocation(winner);
        locations.remove(primary);
        String baseReason = rootReason(winner.getReason());
        winner.setReason(locations.isEmpty()
                ? baseReason
                : baseReason + "\n\nAlso affects: " + String.join(", ", locations));
    }

    private void collectGeneratedLocations(String reason, Set<String> locations) {
        if (reason == null) {
            return;
        }
        for (String line : reason.split("\\R")) {
            String stripped = line.strip();
            if (!stripped.toLowerCase(Locale.ROOT).startsWith("also affects:")) {
                continue;
            }
            String value = stripped.substring(stripped.indexOf(':') + 1);
            Arrays.stream(value.split(","))
                    .map(String::strip)
                    .filter(item -> !item.isBlank())
                    .forEach(locations::add);
        }
    }

    private void addConcreteSecondaryLocation(
            CodeAnalysisIssue winner,
            CodeAnalysisIssue candidate,
            Set<String> locations
    ) {
        if (candidate == winner || candidate.getLineNumber() == null
                || candidate.getLineNumber() <= 1) {
            // Line 1 is the legacy FILE-scope/stale-anchor fallback and is not
            // safe to publish as an additional concrete occurrence.
            return;
        }
        String location = issueLocation(candidate);
        if (!location.isBlank()) {
            locations.add(location);
        }
    }

    private String issueLocation(CodeAnalysisIssue issue) {
        String file = issue.getFilePath() != null ? issue.getFilePath() : "";
        Integer line = issue.getLineNumber();
        return line != null && line > 0 ? file + ":" + line : file;
    }

    // ── Tier 2: category-aware fingerprint ───────────────────────────────

    /**
     * De-duplicate by issue fingerprint — catches issues at different lines
     * that converge to the same content hash + category + normalized title.
     */
    private List<CodeAnalysisIssue> fingerprintDedup(List<CodeAnalysisIssue> issues, String filePath) {
        if (issues.size() < 2) {
            return issues;
        }

        Map<String, CodeAnalysisIssue> byFingerprint = new LinkedHashMap<>();
        int dupCount = 0;
        int missingFingerprintOrdinal = 0;

        for (CodeAnalysisIssue issue : issues) {
            String identity = AnchoredIssueIdentity.forFingerprint(
                    issue,
                    issue.getIssueFingerprint()
            );
            if (identity == null) {
                // No identity means no safe merge. The ordinal keeps map keys deterministic.
                byFingerprint.put("__no_fp_" + missingFingerprintOrdinal++, issue);
                continue;
            }

            if (byFingerprint.containsKey(identity)) {
                CodeAnalysisIssue existing = byFingerprint.get(identity);
                byFingerprint.put(identity, pickBest(existing, issue));
                dupCount++;
            } else {
                byFingerprint.put(identity, issue);
            }
        }

        if (dupCount > 0) {
            log.debug("Fingerprint dedup: removed {} in {}", dupCount, filePath);
        }

        return new ArrayList<>(byFingerprint.values());
    }

    /**
     * Tier 2: category-agnostic content fingerprint dedup.
     * Catches the same issue classified under different categories (e.g. STYLE vs CODE_QUALITY)
     * by using only lineHash + normalizedTitle.
     */
    private List<CodeAnalysisIssue> contentFingerprintDedup(List<CodeAnalysisIssue> issues, String filePath) {
        if (issues.size() < 2) {
            return issues;
        }

        Map<String, CodeAnalysisIssue> byContentFp = new LinkedHashMap<>();
        int dupCount = 0;
        int missingFingerprintOrdinal = 0;

        for (CodeAnalysisIssue issue : issues) {
            String identity = AnchoredIssueIdentity.forFingerprint(
                    issue,
                    issue.getContentFingerprint()
            );
            if (identity == null) {
                byContentFp.put("__no_cfp_" + missingFingerprintOrdinal++, issue);
                continue;
            }

            if (byContentFp.containsKey(identity)) {
                CodeAnalysisIssue existing = byContentFp.get(identity);
                byContentFp.put(identity, pickBest(existing, issue));
                dupCount++;
            } else {
                byContentFp.put(identity, issue);
            }
        }

        if (dupCount > 0) {
            log.debug("Content-fingerprint dedup: removed {} in {}", dupCount, filePath);
        }

        return new ArrayList<>(byContentFp.values());
    }

    /**
     * Pick the best representation of two issues that already have the same exact
     * identity, then promote useful metadata from the other representation. This
     * method never decides whether two issues are equal.
     */
    private CodeAnalysisIssue pickBest(CodeAnalysisIssue a, CodeAnalysisIssue b) {
        CodeAnalysisIssue winner = representationRank(a) >= representationRank(b) ? a : b;
        CodeAnalysisIssue loser = winner == a ? b : a;
        promoteMergedMetadata(winner, loser);
        return winner;
    }

    private long representationRank(CodeAnalysisIssue issue) {
        long rank = 0;
        if (issue.getCodeSnippet() != null && !issue.getCodeSnippet().isBlank()) {
            rank += 1_000_000_000L;
        }
        if (issue.getLineNumber() != null && issue.getLineNumber() > 1) {
            rank += 100_000_000L;
        } else if (issue.getLineNumber() != null && issue.getLineNumber() > 0) {
            rank += 10_000_000L;
        }
        rank += Math.min(validDiffLength(issue.getSuggestedFixDiff()), 1_000_000);
        rank += Math.min(issue.getReason() != null ? issue.getReason().length() : 0, 100_000);
        return rank;
    }

    private void promoteMergedMetadata(CodeAnalysisIssue winner, CodeAnalysisIssue loser) {
        if (severityRank(loser.getSeverity()) > severityRank(winner.getSeverity())) {
            winner.setSeverity(loser.getSeverity());
        }
        if (validDiffLength(loser.getSuggestedFixDiff())
                > validDiffLength(winner.getSuggestedFixDiff())) {
            winner.setSuggestedFixDiff(loser.getSuggestedFixDiff());
            winner.setSuggestedFixDescription(loser.getSuggestedFixDescription());
        } else if ((winner.getSuggestedFixDescription() == null
                || winner.getSuggestedFixDescription().isBlank())
                && loser.getSuggestedFixDescription() != null) {
            winner.setSuggestedFixDescription(loser.getSuggestedFixDescription());
        }
    }

    private int severityRank(IssueSeverity severity) {
        return severity != null ? SEVERITY_ORDER.getOrDefault(severity, 0) : 0;
    }

    private int validDiffLength(String diff) {
        if (diff == null || !DiffSanitizer.isValidDiffFormat(diff)) {
            return 0;
        }
        return diff.length();
    }
}
