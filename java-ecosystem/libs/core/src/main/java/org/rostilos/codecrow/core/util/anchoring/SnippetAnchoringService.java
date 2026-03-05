package org.rostilos.codecrow.core.util.anchoring;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Server-side snippet anchoring: given a code snippet from the LLM, finds the
 * real line number in the actual file content using hash-based matching.
 * <p>
 * This replaces LLM-reported line numbers (which are unreliable since the LLM
 * only sees diffs/snippets, not full files) with server-verified positions.
 * <p>
 * Scope boundaries (function/block end-lines) are <b>not</b> resolved here.
 * Instead, scope-aware reconciliation uses a configurable context-hash radius
 * in {@code computeTrackingHashes()} and {@code IssueReconciliationEngine}
 * to detect body-level changes without needing exact boundary parsing.
 *
 * <h3>Integration point</h3>
 * Called from {@code CodeAnalysisService.createIssueFromData()} after parsing
 * the LLM response and before {@code computeTrackingHashes()}. The anchored
 * line number overrides the LLM's hint.
 *
 * <h3>Thread safety</h3>
 * All methods are stateless and safe to call from any thread.
 */
public final class SnippetAnchoringService {

    private static final Logger log = LoggerFactory.getLogger(SnippetAnchoringService.class);

    private SnippetAnchoringService() {}

    /**
     * Result of snippet anchoring.
     *
     * @param startLine     corrected 1-based start line
     * @param snippetFound  whether the snippet was located in the file
     * @param confidence    0.0–1.0 indicating match quality
     * @param matchStrategy which snippet matching strategy succeeded
     */
    public record AnchorResult(
            int startLine,
            boolean snippetFound,
            float confidence,
            SnippetLocator.Strategy matchStrategy
    ) {
        /** Whether the anchor was successful enough to override the LLM's line number. */
        public boolean shouldOverrideLine() {
            return snippetFound && confidence >= 0.5f;
        }
    }

    /**
     * Anchor a code snippet to its real position in the file.
     *
     * @param codeSnippet the code snippet from the LLM (single or multi-line)
     * @param fileContent the full file content
     * @param hintLine    the LLM-reported line number (used as fallback/tie-breaker)
     * @param filePath    the file path (used only for logging)
     * @return anchoring result with corrected line
     */
    public static AnchorResult anchor(
            String codeSnippet,
            String fileContent,
            int hintLine,
            String filePath
    ) {
        // ── Guard: no snippet or no file ──────────────────────────────
        if (codeSnippet == null || codeSnippet.isBlank()) {
            return unanchored(hintLine);
        }
        if (fileContent == null || fileContent.isEmpty()) {
            return unanchored(hintLine);
        }

        // ── Locate the snippet in the file ───────────────────────────
        SnippetLocator.LocateResult located = SnippetLocator.locate(codeSnippet, fileContent, hintLine);

        if (located.strategy() == SnippetLocator.Strategy.NOT_FOUND) {
            log.debug("Snippet not found in {} (hint line {}). Keeping LLM line number.",
                    filePath, hintLine);
            return unanchored(hintLine);
        }

        int anchoredLine = located.line();

        // ── Log correction ───────────────────────────────────────────
        if (anchoredLine != hintLine) {
            log.info("Snippet anchoring corrected {}:{} → {} (strategy={}, confidence={})",
                    filePath, hintLine, anchoredLine, located.strategy(), located.confidence());
        }

        return new AnchorResult(
                anchoredLine, true, located.confidence(), located.strategy()
        );
    }

    // ─── Helpers ──────────────────────────────────────────────────────

    private static AnchorResult unanchored(int hintLine) {
        return new AnchorResult(
                Math.max(1, hintLine),
                false, 0.0f, SnippetLocator.Strategy.NOT_FOUND
        );
    }
}
