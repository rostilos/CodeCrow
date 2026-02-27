package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.*;

@DisplayName("IssueDeduplicationService")
class IssueDeduplicationServiceTest {

    private IssueDeduplicationService service;

    @BeforeEach
    void setUp() {
        service = new IssueDeduplicationService();
    }

    // ── Helper ───────────────────────────────────────────────────────────

    private CodeAnalysisIssue createIssue(
            String filePath, int line, IssueCategory category,
            IssueSeverity severity, String title, String diff
    ) {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        issue.setFilePath(filePath);
        issue.setLineNumber(line);
        issue.setIssueCategory(category);
        issue.setSeverity(severity);
        issue.setTitle(title);
        issue.setReason("Test reason for " + title);
        issue.setSuggestedFixDiff(diff);
        issue.setSuggestedFixDescription("Fix for " + title);
        // Fingerprint is normally computed by CodeAnalysisService; set manually for tests
        issue.setIssueFingerprint(category.name() + ":" + line + ":" + title.toLowerCase().hashCode());
        return issue;
    }

    private CodeAnalysisIssue createIssue(String filePath, int line, IssueCategory category, IssueSeverity severity, String title) {
        return createIssue(filePath, line, category, severity, title, "--- a/" + filePath + "\n+++ b/" + filePath + "\n- old\n+ new");
    }

    // ── Edge cases ───────────────────────────────────────────────────────

    @Nested
    @DisplayName("Edge cases")
    class EdgeCases {

        @Test
        @DisplayName("should handle null input")
        void shouldHandleNull() {
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(null);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should handle empty list")
        void shouldHandleEmpty() {
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(new ArrayList<>());
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should handle single issue")
        void shouldHandleSingle() {
            List<CodeAnalysisIssue> input = List.of(
                    createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Null check")
            );
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(new ArrayList<>(input));
            assertThat(result).hasSize(1);
        }

        @Test
        @DisplayName("should pass through distinct issues unchanged")
        void shouldPassThroughDistinct() {
            List<CodeAnalysisIssue> input = new ArrayList<>(List.of(
                    createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Null check"),
                    createIssue("App.java", 50, IssueCategory.SECURITY, IssueSeverity.MEDIUM, "SQL injection"),
                    createIssue("Utils.java", 20, IssueCategory.CODE_QUALITY, IssueSeverity.LOW, "Long method")
            ));
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);
            assertThat(result).hasSize(3);
        }
    }

    // ── Tier 1: Structural key dedup ─────────────────────────────────────

    @Nested
    @DisplayName("Tier 1: Structural dedup (file:line:category)")
    class StructuralDedup {

        @Test
        @DisplayName("should remove exact file:line:category duplicate")
        void shouldRemoveExactDuplicate() {
            List<CodeAnalysisIssue> input = new ArrayList<>(List.of(
                    createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.MEDIUM, "Null check v1"),
                    createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Null check v2")
            ));
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);

            assertThat(result).hasSize(1);
            // Higher severity should win
            assertThat(result.get(0).getSeverity()).isEqualTo(IssueSeverity.HIGH);
        }

        @Test
        @DisplayName("should keep issues at different lines")
        void shouldKeepDifferentLines() {
            List<CodeAnalysisIssue> input = new ArrayList<>(List.of(
                    createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Issue A"),
                    createIssue("App.java", 20, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Issue B")
            ));
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);
            assertThat(result).hasSize(2);
        }

        @Test
        @DisplayName("should keep issues in different categories at same line")
        void shouldKeepDifferentCategories() {
            List<CodeAnalysisIssue> input = new ArrayList<>(List.of(
                    createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Bug"),
                    createIssue("App.java", 10, IssueCategory.SECURITY, IssueSeverity.HIGH, "Security")
            ));
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);
            assertThat(result).hasSize(2);
        }

        @Test
        @DisplayName("should keep best diff when merging duplicates")
        void shouldKeepBestDiff() {
            CodeAnalysisIssue noDiff = createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "A", "No suggested fix provided");
            CodeAnalysisIssue withDiff = createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.MEDIUM, "B",
                    "--- a/App.java\n+++ b/App.java\n@@ -10,3 +10,3 @@\n- bad\n+ good");

            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(new ArrayList<>(List.of(noDiff, withDiff)));

            assertThat(result).hasSize(1);
            // HIGH severity wins but should adopt the diff from the MEDIUM one
            assertThat(result.get(0).getSeverity()).isEqualTo(IssueSeverity.HIGH);
            assertThat(result.get(0).getSuggestedFixDiff()).contains("--- a/App.java");
        }
    }

    // ── Tier 2: Whole-file wildcard ──────────────────────────────────────

    @Nested
    @DisplayName("Tier 2: Whole-file wildcard (line ≤ 1)")
    class WholeFileWildcard {

        @Test
        @DisplayName("should absorb specific-line issue when whole-file entry exists first")
        void shouldAbsorbSpecificLineWhenWholeFileFirst() {
            List<CodeAnalysisIssue> input = new ArrayList<>(List.of(
                    createIssue("App.java", 0, IssueCategory.CODE_QUALITY, IssueSeverity.MEDIUM, "Whole file issue"),
                    createIssue("App.java", 42, IssueCategory.CODE_QUALITY, IssueSeverity.LOW, "Specific line issue")
            ));
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getTitle()).isEqualTo("Whole file issue");
        }

        @Test
        @DisplayName("should discard whole-file entry when specific-line exists first")
        void shouldDiscardWholeFileWhenSpecificExists() {
            List<CodeAnalysisIssue> input = new ArrayList<>(List.of(
                    createIssue("App.java", 42, IssueCategory.CODE_QUALITY, IssueSeverity.MEDIUM, "Specific line issue"),
                    createIssue("App.java", 1, IssueCategory.CODE_QUALITY, IssueSeverity.LOW, "Whole file issue")
            ));
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getLineNumber()).isEqualTo(42);
        }

        @Test
        @DisplayName("should not absorb issues of different categories")
        void shouldNotAbsorbDifferentCategories() {
            List<CodeAnalysisIssue> input = new ArrayList<>(List.of(
                    createIssue("App.java", 0, IssueCategory.CODE_QUALITY, IssueSeverity.MEDIUM, "Quality"),
                    createIssue("App.java", 42, IssueCategory.SECURITY, IssueSeverity.HIGH, "Security")
            ));
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);
            assertThat(result).hasSize(2);
        }

        @Test
        @DisplayName("whole-file wildcard should promote severity from absorbed issue")
        void shouldPromoteSeverityFromAbsorbed() {
            List<CodeAnalysisIssue> input = new ArrayList<>(List.of(
                    createIssue("App.java", 0, IssueCategory.BUG_RISK, IssueSeverity.LOW, "Whole file"),
                    createIssue("App.java", 42, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Specific line")
            ));
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);

            assertThat(result).hasSize(1);
            // The surviving whole-file entry should have been promoted to HIGH
        }
    }

    // ── Tier 3: Fingerprint-based ────────────────────────────────────────

    @Nested
    @DisplayName("Tier 3: Fingerprint-based dedup")
    class FingerprintDedup {

        @Test
        @DisplayName("should merge issues with same fingerprint at different lines")
        void shouldMergeSameFingerprint() {
            CodeAnalysisIssue issue1 = createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "NPE risk");
            CodeAnalysisIssue issue2 = createIssue("App.java", 15, IssueCategory.BUG_RISK, IssueSeverity.MEDIUM, "NPE risk 2");
            // Force same fingerprint
            String sharedFp = "shared_fingerprint_abc123";
            issue1.setIssueFingerprint(sharedFp);
            issue2.setIssueFingerprint(sharedFp);

            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(new ArrayList<>(List.of(issue1, issue2)));

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getSeverity()).isEqualTo(IssueSeverity.HIGH);
        }

        @Test
        @DisplayName("should keep issues with different fingerprints")
        void shouldKeepDifferentFingerprints() {
            CodeAnalysisIssue issue1 = createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Issue A");
            CodeAnalysisIssue issue2 = createIssue("App.java", 15, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Issue B");
            issue1.setIssueFingerprint("fp_aaa");
            issue2.setIssueFingerprint("fp_bbb");

            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(new ArrayList<>(List.of(issue1, issue2)));
            assertThat(result).hasSize(2);
        }

        @Test
        @DisplayName("should handle issues with null fingerprint")
        void shouldHandleNullFingerprint() {
            CodeAnalysisIssue issue1 = createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "A");
            CodeAnalysisIssue issue2 = createIssue("App.java", 15, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "B");
            issue1.setIssueFingerprint(null);
            issue2.setIssueFingerprint(null);

            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(new ArrayList<>(List.of(issue1, issue2)));
            assertThat(result).hasSize(2);
        }
    }

    // ── Resolved issues ──────────────────────────────────────────────────

    @Nested
    @DisplayName("Resolved issues")
    class ResolvedIssues {

        @Test
        @DisplayName("should never dedup resolved issues")
        void shouldNeverDedupResolved() {
            CodeAnalysisIssue active = createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Same issue");
            CodeAnalysisIssue resolved = createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Same issue");
            resolved.setResolved(true);

            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(new ArrayList<>(List.of(active, resolved)));

            // Both should survive — resolved issues are never deduped
            assertThat(result).hasSize(2);
        }
    }

    // ── Cross-file isolation ─────────────────────────────────────────────

    @Nested
    @DisplayName("Cross-file isolation")
    class CrossFileIsolation {

        @Test
        @DisplayName("should not dedup issues across different files")
        void shouldNotDedupAcrossFiles() {
            List<CodeAnalysisIssue> input = new ArrayList<>(List.of(
                    createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Null check"),
                    createIssue("Utils.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "Null check")
            ));
            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);
            assertThat(result).hasSize(2);
        }
    }

    // ── Integration: multi-tier ──────────────────────────────────────────

    @Nested
    @DisplayName("Multi-tier integration")
    class MultiTierIntegration {

        @Test
        @DisplayName("should handle mix of structural + fingerprint duplicates")
        void shouldHandleMixedDuplicates() {
            // Structural dup: same file:line:category
            CodeAnalysisIssue a1 = createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.HIGH, "A1");
            CodeAnalysisIssue a2 = createIssue("App.java", 10, IssueCategory.BUG_RISK, IssueSeverity.MEDIUM, "A2");

            // Fingerprint dup: different line but same fingerprint (after structural pass)
            CodeAnalysisIssue b1 = createIssue("App.java", 20, IssueCategory.SECURITY, IssueSeverity.HIGH, "B1");
            CodeAnalysisIssue b2 = createIssue("App.java", 25, IssueCategory.SECURITY, IssueSeverity.MEDIUM, "B2");
            String sharedFp = "shared_fp_security";
            b1.setIssueFingerprint(sharedFp);
            b2.setIssueFingerprint(sharedFp);

            // Unique issue
            CodeAnalysisIssue c = createIssue("App.java", 50, IssueCategory.PERFORMANCE, IssueSeverity.LOW, "C");

            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(
                    new ArrayList<>(List.of(a1, a2, b1, b2, c)));

            // a1+a2 → 1 (structural), b1+b2 → 1 (fingerprint), c → 1
            assertThat(result).hasSize(3);
        }

        @Test
        @DisplayName("should handle large duplicate set")
        void shouldHandleLargeDuplicateSet() {
            List<CodeAnalysisIssue> input = new ArrayList<>();
            // 10 duplicates at same file:line:category
            for (int i = 0; i < 10; i++) {
                input.add(createIssue("Massive.java", 42, IssueCategory.CODE_QUALITY,
                        i < 3 ? IssueSeverity.HIGH : IssueSeverity.LOW, "Dup " + i));
            }
            // Plus 5 unique
            for (int i = 0; i < 5; i++) {
                input.add(createIssue("Massive.java", 100 + i * 10, IssueCategory.BUG_RISK,
                        IssueSeverity.MEDIUM, "Unique " + i));
            }

            List<CodeAnalysisIssue> result = service.deduplicateAtIngestion(input);

            // 10 dupes → 1, 5 unique → 5 = 6 total
            assertThat(result).hasSize(6);
        }
    }
}
