package org.rostilos.codecrow.core.util.tracking;

import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Collection;
import java.util.List;
import java.util.Locale;
import java.util.Objects;

/**
 * Builds the compact, category-independent receipt used for PR issue lineage.
 *
 * <p>The preferred identity is composed from the verified causal fields emitted
 * by the review engine plus exact source/evidence identities. Legacy and cached
 * issues can be reconstructed from their persisted source anchor and narrative.
 * Category and severity are deliberately excluded.</p>
 */
public final class PrIssueLineageFingerprint {

    private PrIssueLineageFingerprint() {
    }

    public static String compute(
            CodeAnalysisIssue issue,
            String triggerCondition,
            String causalPath,
            String observableImpact,
            String claimKind,
            Collection<?> evidenceRefs,
            Collection<?> relatedLocations
    ) {
        Objects.requireNonNull(issue, "issue");

        boolean hasStructuredCausality = hasText(triggerCondition)
                || hasText(causalPath)
                || hasText(observableImpact)
                || hasText(claimKind)
                || hasValues(evidenceRefs)
                || hasValues(relatedLocations);

        String trigger = hasText(triggerCondition)
                ? normalizeText(triggerCondition)
                : normalizeText(issue.getTitle());
        String path = hasText(causalPath) ? normalizeText(causalPath) : "";
        String impact = hasText(observableImpact)
                ? normalizeText(observableImpact)
                : hasStructuredCausality ? "" : normalizeText(issue.getReason());

        String receipt = String.join("\n",
                "path=" + normalizePath(issue.getFilePath()),
                "anchor=" + anchorReceipt(issue),
                "trigger=" + trigger,
                "causal=" + path,
                "impact=" + impact,
                "claim=" + normalizeText(claimKind),
                "evidence=" + normalizeCollection(evidenceRefs),
                "related=" + normalizeCollection(relatedLocations));
        return sha256(receipt);
    }

    /** Reconstruct a deterministic receipt for legacy rows and cache clones. */
    public static String computePersisted(CodeAnalysisIssue issue) {
        return compute(issue, null, null, null, null, List.of(), List.of());
    }

    static String anchorReceipt(CodeAnalysisIssue issue) {
        if (hasText(issue.getLineHash())) {
            return "line-hash:" + issue.getLineHash().strip().toLowerCase(Locale.ROOT);
        }
        if (hasText(issue.getCodeSnippet())) {
            return "snippet:" + sha256(normalizeText(issue.getCodeSnippet()));
        }
        if (issue.getIssueScope() != null) {
            return "scope:" + issue.getIssueScope().name().toLowerCase(Locale.ROOT);
        }
        Integer line = issue.getLineNumber();
        return line != null && line > 0 ? "line:" + line : "unanchored";
    }

    private static boolean hasText(String value) {
        return value != null && !value.isBlank();
    }

    private static boolean hasValues(Collection<?> values) {
        return values != null && values.stream().anyMatch(Objects::nonNull);
    }

    private static String normalizePath(String value) {
        return value == null ? "" : value.strip().replace('\\', '/').toLowerCase(Locale.ROOT);
    }

    private static String normalizeText(String value) {
        return value == null
                ? ""
                : value.strip().replaceAll("\\s+", " ").toLowerCase(Locale.ROOT);
    }

    private static String normalizeCollection(Collection<?> values) {
        if (values == null || values.isEmpty()) {
            return "";
        }
        return values.stream()
                .filter(Objects::nonNull)
                .map(String::valueOf)
                .map(PrIssueLineageFingerprint::normalizeText)
                .filter(value -> !value.isBlank())
                .distinct()
                .sorted()
                .reduce((left, right) -> left + "|" + right)
                .orElse("");
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(64);
            for (byte b : digest) {
                hex.append(String.format("%02x", b & 0xff));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }
}
