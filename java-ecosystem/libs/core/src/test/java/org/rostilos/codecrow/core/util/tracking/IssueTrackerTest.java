package org.rostilos.codecrow.core.util.tracking;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;

import java.util.List;
import java.util.Collections;

import static org.junit.jupiter.api.Assertions.*;

@DisplayName("IssueTracker")
class IssueTrackerTest {

    /** Simple Trackable implementation for testing. */
    private record TestIssue(
            String fingerprint,
            Integer line,
            String lineHash,
            String filePath
    ) implements Trackable {
        @Override public String getIssueFingerprint() { return fingerprint; }
        @Override public Integer getLine() { return line; }
        @Override public String getLineHash() { return lineHash; }
        @Override public String getFilePath() { return filePath; }
    }

    @Nested
    @DisplayName("Pass 1: EXACT (fingerprint + line + lineHash)")
    class ExactPass {

        @Test
        @DisplayName("identical issue matches with EXACT confidence")
        void identicalIssueExactMatch() {
            TestIssue raw = new TestIssue("fp1", 10, "hash_a", "Foo.java");
            TestIssue base = new TestIssue("fp1", 10, "hash_a", "Foo.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(List.of(raw), List.of(base));

            assertTrue(result.isComplete());
            assertEquals(1, result.matchedCount());
            assertEquals(TrackingConfidence.EXACT, result.getConfidence(raw));
            assertSame(base, result.getBaseFor(raw));
        }

        @Test
        @DisplayName("same fingerprint but different line → not EXACT")
        void differentLineNotExact() {
            TestIssue raw = new TestIssue("fp1", 15, "hash_a", "Foo.java");
            TestIssue base = new TestIssue("fp1", 10, "hash_a", "Foo.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(List.of(raw), List.of(base));

            // Should match in SHIFTED pass (fingerprint + lineHash)
            assertTrue(result.isComplete());
            assertEquals(TrackingConfidence.SHIFTED, result.getConfidence(raw));
        }
    }

    @Nested
    @DisplayName("Pass 2: SHIFTED (fingerprint + lineHash)")
    class ShiftedPass {

        @Test
        @DisplayName("same content at different line matches as SHIFTED")
        void shiftedLineMatch() {
            TestIssue raw = new TestIssue("fp1", 20, "hash_a", "Foo.java");
            TestIssue base = new TestIssue("fp1", 10, "hash_a", "Foo.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(List.of(raw), List.of(base));

            assertTrue(result.isComplete());
            assertEquals(TrackingConfidence.SHIFTED, result.getConfidence(raw));
        }
    }

    @Nested
    @DisplayName("Pass 3: EDITED (fingerprint + line)")
    class EditedPass {

        @Test
        @DisplayName("same line but different hash matches as EDITED")
        void editedContentMatch() {
            TestIssue raw = new TestIssue("fp1", 10, "hash_new", "Foo.java");
            TestIssue base = new TestIssue("fp1", 10, "hash_old", "Foo.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(List.of(raw), List.of(base));

            assertTrue(result.isComplete());
            assertEquals(TrackingConfidence.EDITED, result.getConfidence(raw));
        }
    }

    @Nested
    @DisplayName("Pass 4: WEAK (fingerprint only)")
    class WeakPass {

        @Test
        @DisplayName("same fingerprint but different line and hash matches as WEAK")
        void weakMatch() {
            TestIssue raw = new TestIssue("fp1", 50, "hash_new", "Foo.java");
            TestIssue base = new TestIssue("fp1", 10, "hash_old", "Foo.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(List.of(raw), List.of(base));

            assertTrue(result.isComplete());
            assertEquals(TrackingConfidence.WEAK, result.getConfidence(raw));
        }

        @Test
        @DisplayName("different fingerprint → no match")
        void noMatchDifferentFingerprint() {
            TestIssue raw = new TestIssue("fp_new", 10, "hash_a", "Foo.java");
            TestIssue base = new TestIssue("fp_old", 10, "hash_a", "Foo.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(List.of(raw), List.of(base));

            assertFalse(result.isComplete());
            assertEquals(0, result.matchedCount());
            assertEquals(1, result.getUnmatchedRaws().size());
            assertEquals(1, result.getUnmatchedBases().size());
        }
    }

    @Nested
    @DisplayName("Multiple issues")
    class MultipleIssues {

        @Test
        @DisplayName("three issues at different confidence levels")
        void multipleConfidenceLevels() {
            // EXACT match
            TestIssue raw1 = new TestIssue("fp1", 10, "h1", "A.java");
            TestIssue base1 = new TestIssue("fp1", 10, "h1", "A.java");

            // SHIFTED match (same fingerprint+hash, different line)
            TestIssue raw2 = new TestIssue("fp2", 30, "h2", "B.java");
            TestIssue base2 = new TestIssue("fp2", 20, "h2", "B.java");

            // WEAK match (same fingerprint, different line+hash)
            TestIssue raw3 = new TestIssue("fp3", 50, "h3_new", "C.java");
            TestIssue base3 = new TestIssue("fp3", 10, "h3_old", "C.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(
                    List.of(raw1, raw2, raw3), List.of(base1, base2, base3));

            assertTrue(result.isComplete());
            assertEquals(3, result.matchedCount());
            assertEquals(TrackingConfidence.EXACT, result.getConfidence(raw1));
            assertEquals(TrackingConfidence.SHIFTED, result.getConfidence(raw2));
            assertEquals(TrackingConfidence.WEAK, result.getConfidence(raw3));
        }

        @Test
        @DisplayName("unmatched raw = new issue, unmatched base = resolved issue")
        void unmatchedOnBothSides() {
            TestIssue rawExisting = new TestIssue("fp1", 10, "h1", "A.java");
            TestIssue rawNew = new TestIssue("fp_new", 20, "h_new", "B.java");
            TestIssue baseExisting = new TestIssue("fp1", 10, "h1", "A.java");
            TestIssue baseGone = new TestIssue("fp_gone", 30, "h_gone", "C.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(
                    List.of(rawExisting, rawNew), List.of(baseExisting, baseGone));

            assertEquals(1, result.matchedCount());
            assertEquals(1, result.getUnmatchedRaws().size());
            assertSame(rawNew, result.getUnmatchedRaws().get(0));
            assertEquals(1, result.getUnmatchedBases().size());
            assertSame(baseGone, result.getUnmatchedBases().get(0));
        }

        @Test
        @DisplayName("multiple candidates with same key prefer closest line")
        void closestLinePreference() {
            // Two bases with same fingerprint+hash at lines 10 and 50
            TestIssue base1 = new TestIssue("fp1", 10, "h1", "A.java");
            TestIssue base2 = new TestIssue("fp1", 50, "h1", "A.java");
            // Raw is at line 48 → should match base2 (closer)
            TestIssue raw = new TestIssue("fp1", 48, "h1", "A.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(
                    List.of(raw), List.of(base1, base2));

            assertEquals(1, result.matchedCount());
            assertSame(base2, result.getBaseFor(raw), "Should match closest base by line number");
        }
    }

    @Nested
    @DisplayName("Edge cases")
    class EdgeCases {

        @Test
        @DisplayName("empty raw list")
        void emptyRawList() {
            TestIssue base = new TestIssue("fp1", 10, "h1", "A.java");
            Tracking<TestIssue, TestIssue> result = IssueTracker.track(
                    Collections.emptyList(), List.of(base));

            assertTrue(result.isComplete()); // no raws to match
            assertEquals(0, result.matchedCount());
            assertEquals(1, result.getUnmatchedBases().size());
        }

        @Test
        @DisplayName("empty base list")
        void emptyBaseList() {
            TestIssue raw = new TestIssue("fp1", 10, "h1", "A.java");
            Tracking<TestIssue, TestIssue> result = IssueTracker.track(
                    List.of(raw), Collections.emptyList());

            assertFalse(result.isComplete());
            assertEquals(0, result.matchedCount());
            assertEquals(1, result.getUnmatchedRaws().size());
        }

        @Test
        @DisplayName("both lists empty")
        void bothEmpty() {
            Tracking<TestIssue, TestIssue> result = IssueTracker.track(
                    Collections.emptyList(), Collections.emptyList());
            assertTrue(result.isComplete());
            assertEquals(0, result.matchedCount());
        }

        @Test
        @DisplayName("null fingerprint issues are skipped (not matched)")
        void nullFingerprint() {
            TestIssue raw = new TestIssue(null, 10, "h1", "A.java");
            TestIssue base = new TestIssue(null, 10, "h1", "A.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(
                    List.of(raw), List.of(base));

            assertEquals(0, result.matchedCount());
        }

        @Test
        @DisplayName("null line number still matches via fingerprint-only pass")
        void nullLineNumber() {
            TestIssue raw = new TestIssue("fp1", null, null, "A.java");
            TestIssue base = new TestIssue("fp1", null, null, "A.java");

            Tracking<TestIssue, TestIssue> result = IssueTracker.track(
                    List.of(raw), List.of(base));

            // Should match in WEAK pass (fingerprint only)
            assertTrue(result.isComplete());
            assertEquals(TrackingConfidence.WEAK, result.getConfidence(raw));
        }

        @Test
        @DisplayName("large batch performance: 1000 issues tracked quickly")
        void largeBatch() {
            int n = 1000;
            List<TestIssue> raws = new java.util.ArrayList<>();
            List<TestIssue> bases = new java.util.ArrayList<>();

            for (int i = 0; i < n; i++) {
                raws.add(new TestIssue("fp" + i, i + 5, "hash" + i, "File" + (i % 50) + ".java"));
                bases.add(new TestIssue("fp" + i, i, "hash" + i, "File" + (i % 50) + ".java"));
            }

            long start = System.nanoTime();
            Tracking<TestIssue, TestIssue> result = IssueTracker.track(raws, bases);
            long elapsed = System.nanoTime() - start;

            assertTrue(result.isComplete());
            assertEquals(n, result.matchedCount());
            // Should complete in under 500ms even on slow hardware
            assertTrue(elapsed < 500_000_000L,
                    "Tracking 1000 issues should be fast, took " + (elapsed / 1_000_000) + "ms");
        }
    }
}
