package org.rostilos.codecrow.core.util.tracking;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;

import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

@DisplayName("Tracking")
class TrackingTest {

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

    @Test
    @DisplayName("new tracking has all issues unmatched")
    void newTrackingAllUnmatched() {
        TestIssue raw1 = new TestIssue("fp1", 10, "h1", "a.java");
        TestIssue base1 = new TestIssue("fp1", 10, "h1", "a.java");

        Tracking<TestIssue, TestIssue> tracking = new Tracking<>(List.of(raw1), List.of(base1));

        assertEquals(1, tracking.rawCount());
        assertEquals(1, tracking.baseCount());
        assertEquals(0, tracking.matchedCount());
        assertFalse(tracking.isComplete());
        assertEquals(1, tracking.getUnmatchedRaws().size());
        assertEquals(1, tracking.getUnmatchedBases().size());
    }

    @Test
    @DisplayName("match() links raw and base")
    void matchLinksRawAndBase() {
        TestIssue raw1 = new TestIssue("fp1", 10, "h1", "a.java");
        TestIssue base1 = new TestIssue("fp1", 10, "h1", "a.java");

        Tracking<TestIssue, TestIssue> tracking = new Tracking<>(List.of(raw1), List.of(base1));
        tracking.match(raw1, base1, TrackingConfidence.EXACT);

        assertTrue(tracking.isRawMatched(raw1));
        assertTrue(tracking.isBaseMatched(base1));
        assertSame(base1, tracking.getBaseFor(raw1));
        assertSame(raw1, tracking.getRawFor(base1));
        assertEquals(TrackingConfidence.EXACT, tracking.getConfidence(raw1));
        assertTrue(tracking.isComplete());
        assertEquals(1, tracking.matchedCount());
        assertTrue(tracking.getUnmatchedRaws().isEmpty());
        assertTrue(tracking.getUnmatchedBases().isEmpty());
    }

    @Test
    @DisplayName("match() throws for duplicate raw match")
    void matchThrowsForDuplicateRaw() {
        TestIssue raw1 = new TestIssue("fp1", 10, "h1", "a.java");
        TestIssue base1 = new TestIssue("fp1", 10, "h1", "a.java");
        TestIssue base2 = new TestIssue("fp2", 20, "h2", "a.java");

        Tracking<TestIssue, TestIssue> tracking = new Tracking<>(List.of(raw1), List.of(base1, base2));
        tracking.match(raw1, base1, TrackingConfidence.EXACT);

        assertThrows(IllegalStateException.class, () ->
                tracking.match(raw1, base2, TrackingConfidence.SHIFTED));
    }

    @Test
    @DisplayName("match() throws for duplicate base match")
    void matchThrowsForDuplicateBase() {
        TestIssue raw1 = new TestIssue("fp1", 10, "h1", "a.java");
        TestIssue raw2 = new TestIssue("fp2", 20, "h2", "a.java");
        TestIssue base1 = new TestIssue("fp1", 10, "h1", "a.java");

        Tracking<TestIssue, TestIssue> tracking = new Tracking<>(List.of(raw1, raw2), List.of(base1));
        tracking.match(raw1, base1, TrackingConfidence.EXACT);

        assertThrows(IllegalStateException.class, () ->
                tracking.match(raw2, base1, TrackingConfidence.WEAK));
    }

    @Test
    @DisplayName("getMatchedPairs returns all matches with confidence")
    void getMatchedPairs() {
        TestIssue raw1 = new TestIssue("fp1", 10, "h1", "a.java");
        TestIssue raw2 = new TestIssue("fp2", 20, "h2", "a.java");
        TestIssue base1 = new TestIssue("fp1", 10, "h1", "a.java");
        TestIssue base2 = new TestIssue("fp2", 20, "h2", "a.java");

        Tracking<TestIssue, TestIssue> tracking = new Tracking<>(
                List.of(raw1, raw2), List.of(base1, base2));
        tracking.match(raw1, base1, TrackingConfidence.EXACT);
        tracking.match(raw2, base2, TrackingConfidence.SHIFTED);

        List<Tracking.MatchedPair<TestIssue, TestIssue>> pairs = tracking.getMatchedPairs();
        assertEquals(2, pairs.size());
    }

    @Test
    @DisplayName("getConfidence returns NONE for unmatched raw")
    void confidenceNoneForUnmatched() {
        TestIssue raw1 = new TestIssue("fp1", 10, "h1", "a.java");
        Tracking<TestIssue, TestIssue> tracking = new Tracking<>(List.of(raw1), List.of());
        assertEquals(TrackingConfidence.NONE, tracking.getConfidence(raw1));
    }

    @Test
    @DisplayName("empty tracking is immediately complete")
    void emptyTrackingIsComplete() {
        Tracking<TestIssue, TestIssue> tracking = new Tracking<>(List.of(), List.of());
        assertTrue(tracking.isComplete());
        assertEquals(0, tracking.rawCount());
        assertEquals(0, tracking.baseCount());
    }

    @Test
    @DisplayName("unmatched bases are returned correctly")
    void unmatchedBases() {
        TestIssue raw1 = new TestIssue("fp1", 10, "h1", "a.java");
        TestIssue base1 = new TestIssue("fp1", 10, "h1", "a.java");
        TestIssue base2 = new TestIssue("fp2", 20, "h2", "a.java");

        Tracking<TestIssue, TestIssue> tracking = new Tracking<>(
                List.of(raw1), List.of(base1, base2));
        tracking.match(raw1, base1, TrackingConfidence.EXACT);

        List<TestIssue> unmatchedBases = tracking.getUnmatchedBases();
        assertEquals(1, unmatchedBases.size());
        assertSame(base2, unmatchedBases.get(0));
    }
}
