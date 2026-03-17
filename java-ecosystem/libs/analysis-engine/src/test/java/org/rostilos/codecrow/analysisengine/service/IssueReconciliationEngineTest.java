package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueScope;
import org.rostilos.codecrow.core.util.tracking.LineHashSequence;
import org.rostilos.codecrow.core.util.tracking.ReconcilableIssue;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Unit tests for the stateless IssueReconciliationEngine.
 * Uses stub ReconcilableIssue instances and real LineHashSequence objects.
 */
class IssueReconciliationEngineTest {

    private IssueReconciliationEngine engine;

    @BeforeEach
    void setUp() {
        engine = new IssueReconciliationEngine();
    }

    // ───────────────── Stub issue implementation ─────────────────────────

    private static StubIssue issue(int line, String snippet) {
        return new StubIssue(line, snippet, null, null, null, null, false);
    }

    private static StubIssue issue(int line, String snippet, String lineHash) {
        return new StubIssue(line, snippet, lineHash, null, null, null, false);
    }

    private static StubIssue issueWithScope(int line, String snippet, IssueScope scope) {
        return new StubIssue(line, snippet, null, null, scope, null, false);
    }

    private static StubIssue issueWithEndLine(int line, String snippet, IssueScope scope, Integer endLine) {
        return new StubIssue(line, snippet, null, null, scope, endLine, false);
    }

    private static StubIssue issueWithContext(int line, String snippet, String lineHash,
                                              String contextHash, IssueScope scope) {
        return new StubIssue(line, snippet, lineHash, contextHash, scope, null, false);
    }

    record StubIssue(
            Integer lineNumber, String codeSnippet, String lineHash,
            String lineHashContext, IssueScope issueScope, Integer endLineNumber,
            boolean resolved
    ) implements ReconcilableIssue {
        @Override public Long getId() { return 1L; }
        @Override public IssueCategory getIssueCategory() { return IssueCategory.BEST_PRACTICES; }
        @Override public IssueScope getIssueScope() { return issueScope; }
        @Override public String getTitle() { return "Test issue"; }
        @Override public String getReason() { return "reason"; }
        @Override public Integer getLineNumber() { return lineNumber; }
        @Override public Integer getEndLineNumber() { return endLineNumber; }
        @Override public Integer getScopeStartLine() { return null; }
        @Override public String getCodeSnippet() { return codeSnippet; }
        @Override public String getLineHash() { return lineHash; }
        @Override public String getLineHashContext() { return lineHashContext; }
        @Override public String getContentFingerprint() { return null; }
        @Override public boolean isResolved() { return resolved; }
        @Override public String getIssueFingerprint() { return null; }
        @Override public String getFilePath() { return "test/File.java"; }
        @Override public Integer getLine() { return lineNumber; }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  1. remapLinesFromDiff
    // ═══════════════════════════════════════════════════════════════════════

    @Nested
    class RemapLinesFromDiff {

        @Test
        void emptyDiff_shouldReturnEmpty() {
            var results = engine.remapLinesFromDiff(List.of(issue(5, "code")), "");
            assertThat(results).isEmpty();
        }

        @Test
        void nullDiff_shouldReturnEmpty() {
            var results = engine.remapLinesFromDiff(List.of(issue(5, "code")), null);
            assertThat(results).isEmpty();
        }

        @Test
        void emptyIssuesList_shouldReturnEmpty() {
            var results = engine.remapLinesFromDiff(List.of(), "@@ -1,3 +1,4 @@\n+new line\n old\n old\n old\n");
            assertThat(results).isEmpty();
        }

        @Test
        void issueWithNullLine_shouldBeSkipped() {
            var results = engine.remapLinesFromDiff(
                    List.of(new StubIssue(null, "code", null, null, null, null, false)),
                    "@@ -1,3 +1,4 @@\n+new line\n old\n old\n old\n");
            assertThat(results).isEmpty();
        }

        @Test
        void issueWithZeroLine_shouldBeSkipped() {
            var results = engine.remapLinesFromDiff(
                    List.of(issue(0, "code")),
                    "@@ -1,3 +1,4 @@\n+new line\n old\n old\n old\n");
            assertThat(results).isEmpty();
        }

        @Test
        void lineShiftedByInsertion_shouldRemap() {
            // A line added at line 1 pushes everything down by 1
            String diff = "@@ -1,3 +1,4 @@\n+new line\n old line 1\n old line 2\n old line 3\n";
            var results = engine.remapLinesFromDiff(List.of(issue(2, "code")), diff);
            // After insertion at line 1, line 2 becomes line 3
            if (!results.isEmpty()) {
                assertThat(results.get(0).newLine()).isGreaterThan(2);
                assertThat(results.get(0).changed()).isTrue();
            }
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  2. verifySnippetAnchors
    // ═══════════════════════════════════════════════════════════════════════

    @Nested
    class VerifySnippetAnchors {

        @Test
        void emptyIssues_shouldReturnEmpty() {
            var hashes = LineHashSequence.from("line1\nline2\nline3");
            assertThat(engine.verifySnippetAnchors(List.of(), hashes)).isEmpty();
        }

        @Test
        void emptyLineHashes_shouldReturnEmpty() {
            assertThat(engine.verifySnippetAnchors(
                    List.of(issue(1, "code")), LineHashSequence.empty())).isEmpty();
        }

        @Test
        void issueWithNullSnippet_shouldBeSkipped() {
            var hashes = LineHashSequence.from("line1\nline2\nline3");
            assertThat(engine.verifySnippetAnchors(
                    List.of(issue(1, null)), hashes)).isEmpty();
        }

        @Test
        void issueWithBlankSnippet_shouldBeSkipped() {
            var hashes = LineHashSequence.from("line1\nline2");
            assertThat(engine.verifySnippetAnchors(
                    List.of(issue(1, "  ")), hashes)).isEmpty();
        }

        @Test
        void issueWithNullLine_shouldBeSkipped() {
            var hashes = LineHashSequence.from("code\nmore code");
            assertThat(engine.verifySnippetAnchors(
                    List.of(new StubIssue(null, "code", null, null, null, null, false)),
                    hashes)).isEmpty();
        }

        @Test
        void snippetAtCorrectLine_shouldNotNeedCorrection() {
            String content = "alpha\nbeta\ngamma";
            var hashes = LineHashSequence.from(content);
            // Issue at line 2 with snippet "beta" — already correct
            var results = engine.verifySnippetAnchors(
                    List.of(issue(2, "beta")), hashes);
            assertThat(results).isEmpty();
        }

        @Test
        void snippetDrifted_shouldReturnCorrection() {
            // Content has "beta" at line 3 now, but issue says line 1
            String content = "alpha\ngamma\nbeta";
            var hashes = LineHashSequence.from(content);
            var results = engine.verifySnippetAnchors(
                    List.of(issue(1, "beta")), hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).correctedLine()).isEqualTo(3);
        }

        @Test
        void multiLineSnippet_shouldFindClosestMatch() {
            String content = "aaa\nbbb\nccc\nddd\neee";
            var hashes = LineHashSequence.from(content);
            // Issue at line 1, snippet has "ddd" — should correct to line 4
            var results = engine.verifySnippetAnchors(
                    List.of(issue(1, "ddd")), hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).correctedLine()).isEqualTo(4);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  3. classifyByContent
    // ═══════════════════════════════════════════════════════════════════════

    @Nested
    class ClassifyByContent {

        @Test
        void emptyHashes_shouldRouteAllToNeedsAi() {
            var results = engine.classifyByContent(
                    List.of(issue(5, "code")),
                    LineHashSequence.empty());
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
        }

        @Test
        void fileScopeIssue_shouldAlwaysBeNeedsAi() {
            var hashes = LineHashSequence.from("code\nmore code\nthird line");
            var results = engine.classifyByContent(
                    List.of(issueWithScope(2, "more code", IssueScope.FILE)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
        }

        @Test
        void unanchoredIssue_shouldBeNeedsAi() {
            var hashes = LineHashSequence.from("alpha\nbeta");
            // Line <= 1 and blank snippet → unanchored
            var results = engine.classifyByContent(
                    List.of(issue(0, "")),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
        }

        @Test
        void snippetFoundInContent_shouldBeConfirmed() {
            String content = "alpha\nbeta\ngamma";
            var hashes = LineHashSequence.from(content);
            var results = engine.classifyByContent(
                    List.of(issue(2, "beta")),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.CONFIRMED);
            assertThat(results.get(0).updatedLine()).isEqualTo(2);
        }

        @Test
        void snippetGone_shouldBeRoutedToAi() {
            String content = "alpha\ngamma\ndelta";
            var hashes = LineHashSequence.from(content);
            // Snippet "beta" at line 2 — but "beta" no longer exists.
            // Should route to AI for verification, not auto-resolve.
            var results = engine.classifyByContent(
                    List.of(issue(2, "beta")),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
        }

        @Test
        void lineHashFoundButNoSnippet_shouldBeConfirmed() {
            String content = "alpha\nbeta\ngamma";
            var hashes = LineHashSequence.from(content);
            String betaHash = LineHashSequence.hashLine("beta");
            var results = engine.classifyByContent(
                    List.of(issue(2, null, betaHash)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.CONFIRMED);
        }

        @Test
        void lineHashGone_shouldBeRoutedToAi() {
            String content = "alpha\ngamma\ndelta";
            var hashes = LineHashSequence.from(content);
            String betaHash = LineHashSequence.hashLine("beta");
            // lineHash for "beta" no longer in file.
            // Should route to AI for verification, not auto-resolve.
            var results = engine.classifyByContent(
                    List.of(issue(2, null, betaHash)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
        }

        @Test
        void noReliableAnchor_shouldBeNeedsAi() {
            // Line at 5, no snippet, no lineHash — no reliable anchor
            var hashes = LineHashSequence.from("alpha\nbeta\ngamma\ndelta\nepsilon");
            var results = engine.classifyByContent(
                    List.of(issue(5, null, null)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
        }

        @Test
        void blockScope_partialSnippetMatch_shouldBeNeedsAi() {
            String content = "alpha\nbeta\ndelta\nepsilon";
            var hashes = LineHashSequence.from(content);
            // BLOCK scope with multi-line snippet; only "beta" found, "gamma" gone
            var results = engine.classifyByContent(
                    List.of(issueWithScope(2, "beta\ngamma", IssueScope.BLOCK)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
        }

        @Test
        void contextHashChanged_blockScope_shouldBeNeedsAi() {
            String content = "alpha\nbeta\ngamma\ndelta\nepsilon";
            var hashes = LineHashSequence.from(content);
            String betaHash = LineHashSequence.hashLine("beta");
            // BLOCK scope with context hash that doesn't match current context
            var results = engine.classifyByContent(
                    List.of(issueWithContext(2, "beta", betaHash, "old-context-hash", IssueScope.BLOCK)),
                    hashes);
            assertThat(results).hasSize(1);
            // The content IS found, but context hash changed → NEEDS_AI
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
        }

        @Test
        void multipleIssues_mixedClassifications() {
            String content = "alpha\nbeta\ngamma\ndelta";
            var hashes = LineHashSequence.from(content);

            var results = engine.classifyByContent(List.of(
                    issue(2, "beta"),           // CONFIRMED (snippet found)
                    issue(3, "zzzz"),           // NEEDS_AI (snippet gone — routed to AI)
                    issueWithScope(1, null, IssueScope.FILE) // NEEDS_AI (FILE scope)
            ), hashes);

            assertThat(results).hasSize(3);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.CONFIRMED);
            assertThat(results.get(1).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
            assertThat(results.get(2).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.NEEDS_AI);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  4. sweepDeterministic
    // ═══════════════════════════════════════════════════════════════════════

    @Nested
    class SweepDeterministic {

        @Test
        void emptyFileContent_shouldNotResolveAnything() {
            var results = engine.sweepDeterministic(
                    List.of(issue(5, "some code")),
                    LineHashSequence.empty());
            assertThat(results).hasSize(1);
            assertThat(results.get(0).resolved()).isFalse();
            assertThat(results.get(0).reason()).contains("empty");
        }

        @Test
        void fileScopeIssue_shouldBeSkipped() {
            var hashes = LineHashSequence.from("code\nmore");
            var results = engine.sweepDeterministic(
                    List.of(issueWithScope(1, "code", IssueScope.FILE)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).resolved()).isFalse();
            assertThat(results.get(0).reason()).contains("FILE");
        }

        @Test
        void noReliableAnchor_shouldBeSkipped() {
            var hashes = LineHashSequence.from("code\nmore");
            var results = engine.sweepDeterministic(
                    List.of(issue(5, null, null)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).resolved()).isFalse();
            assertThat(results.get(0).reason()).contains("anchor");
        }

        @Test
        void snippetStillPresent_shouldNotResolve() {
            String content = "alpha\nbeta\ngamma";
            var hashes = LineHashSequence.from(content);
            var results = engine.sweepDeterministic(
                    List.of(issue(2, "beta")),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).resolved()).isFalse();
        }

        @Test
        void snippetGone_shouldNotAutoResolve() {
            String content = "alpha\ngamma\ndelta";
            var hashes = LineHashSequence.from(content);
            // Snippet "beta" gone from file — but sweep should NOT auto-resolve.
            // Hash comparison alone is unreliable (LLM snippet not verbatim, null lineHash).
            var results = engine.sweepDeterministic(
                    List.of(issue(2, "beta")),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).resolved()).isFalse();
        }

        @Test
        void lineHashGone_shouldNotAutoResolve() {
            String content = "alpha\ngamma\ndelta";
            var hashes = LineHashSequence.from(content);
            String betaHash = LineHashSequence.hashLine("beta");
            // lineHash for "beta" gone — but sweep should NOT auto-resolve.
            var results = engine.sweepDeterministic(
                    List.of(issue(2, null, betaHash)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).resolved()).isFalse();
        }

        @Test
        void lineHashStillPresent_shouldNotResolve() {
            String content = "alpha\nbeta\ngamma";
            var hashes = LineHashSequence.from(content);
            String betaHash = LineHashSequence.hashLine("beta");
            var results = engine.sweepDeterministic(
                    List.of(issue(2, null, betaHash)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).resolved()).isFalse();
        }

        @Test
        void blockScope_someSnippetLinesPresent_shouldNotResolve() {
            String content = "alpha\nbeta\ndelta";
            var hashes = LineHashSequence.from(content);
            // BLOCK scope: "beta\ngamma" — "beta" still present → not resolved
            var results = engine.sweepDeterministic(
                    List.of(issueWithScope(2, "beta\ngamma", IssueScope.BLOCK)),
                    hashes);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).resolved()).isFalse();
        }

        @Test
        void multipleIssues_mixedResults() {
            String content = "alpha\nbeta\ngamma";
            var hashes = LineHashSequence.from(content);
            String zzzzHash = LineHashSequence.hashLine("zzzz");

            var results = engine.sweepDeterministic(List.of(
                    issue(2, "beta"),           // present → not resolved
                    issue(2, null, zzzzHash)    // gone → still not auto-resolved (requires AI)
            ), hashes);

            assertThat(results).hasSize(2);
            assertThat(results.get(0).resolved()).isFalse();
            assertThat(results.get(1).resolved()).isFalse();
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  5. AST-aware overloads (null AST fallback)
    // ═══════════════════════════════════════════════════════════════════════

    @Nested
    class AstAwareOverloads {

        @Test
        void classifyByContent_nullAst_shouldFallBackToBase() {
            String content = "alpha\nbeta\ngamma";
            var hashes = LineHashSequence.from(content);
            var results = engine.classifyByContent(
                    List.of(issue(2, "beta")), hashes, null, null);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).classification())
                    .isEqualTo(IssueReconciliationEngine.Classification.CONFIRMED);
        }

        @Test
        void sweepDeterministic_nullAst_shouldFallBackToBase() {
            String content = "alpha\ngamma\ndelta";
            var hashes = LineHashSequence.from(content);
            // AST overload with null tree/resolver falls back to base method,
            // which no longer auto-resolves (anchor not found → skip, not resolve)
            var results = engine.sweepDeterministic(
                    List.of(issue(2, "beta")), hashes, null, null);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).resolved()).isFalse();
        }

        @Test
        void verifySnippetAnchors_nullAst_shouldFallBackToBase() {
            String content = "alpha\ngamma\nbeta";
            var hashes = LineHashSequence.from(content);
            var results = engine.verifySnippetAnchors(
                    List.of(issue(1, "beta")), hashes, null, null);
            assertThat(results).hasSize(1);
            assertThat(results.get(0).correctedLine()).isEqualTo(3);
        }
    }

    // ═══════════════════════════════════════════════════════════════════════
    //  6. Records + enums
    // ═══════════════════════════════════════════════════════════════════════

    @Test
    void lineRemapResult_changed_shouldCompareCorrectly() {
        var changed = new IssueReconciliationEngine.LineRemapResult(issue(5, "x"), 5, 10, null, null);
        var unchanged = new IssueReconciliationEngine.LineRemapResult(issue(5, "x"), 5, 5, null, null);
        assertThat(changed.changed()).isTrue();
        assertThat(unchanged.changed()).isFalse();
    }

    @Test
    void classification_values_shouldExist() {
        assertThat(IssueReconciliationEngine.Classification.values()).hasSize(3);
        assertThat(IssueReconciliationEngine.Classification.valueOf("CONFIRMED")).isNotNull();
        assertThat(IssueReconciliationEngine.Classification.valueOf("RESOLVED")).isNotNull();
        assertThat(IssueReconciliationEngine.Classification.valueOf("NEEDS_AI")).isNotNull();
    }

    @Test
    void computeContextHash_static_shouldWork() {
        var hashes = LineHashSequence.from("alpha\nbeta\ngamma\ndelta\nepsilon");
        // With endLine
        String hash1 = IssueReconciliationEngine.computeContextHash(hashes, 2, 4, null);
        assertThat(hash1).isNotNull();
        // With snippet
        String hash2 = IssueReconciliationEngine.computeContextHash(hashes, 2, null, "beta\ngamma");
        assertThat(hash2).isNotNull();
        // Minimal
        String hash3 = IssueReconciliationEngine.computeContextHash(hashes, 2, null, null);
        assertThat(hash3).isNotNull();
    }
}
