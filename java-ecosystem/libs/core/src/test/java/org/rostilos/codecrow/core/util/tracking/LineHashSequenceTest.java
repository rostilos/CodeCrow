package org.rostilos.codecrow.core.util.tracking;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;

import static org.junit.jupiter.api.Assertions.*;

@DisplayName("LineHashSequence")
class LineHashSequenceTest {

    @Test
    @DisplayName("empty() returns sequence with zero lines")
    void emptyReturnsZeroLines() {
        LineHashSequence seq = LineHashSequence.empty();
        assertEquals(0, seq.getLineCount());
        assertNull(seq.getHashForLine(1));
    }

    @Test
    @DisplayName("from() parses lines and computes hashes")
    void fromParsesContent() {
        String content = "line one\nline two\nline three\n";
        LineHashSequence seq = LineHashSequence.from(content);
        assertEquals(3, seq.getLineCount());
        assertNotNull(seq.getHashForLine(1));
        assertNotNull(seq.getHashForLine(2));
        assertNotNull(seq.getHashForLine(3));
    }

    @Test
    @DisplayName("identical lines produce the same hash")
    void identicalLinesProduceSameHash() {
        String content = "duplicate\nother\nduplicate\n";
        LineHashSequence seq = LineHashSequence.from(content);
        assertEquals(seq.getHashForLine(1), seq.getHashForLine(3));
    }

    @Test
    @DisplayName("different lines produce different hashes")
    void differentLinesProduceDifferentHashes() {
        String content = "alpha\nbeta\n";
        LineHashSequence seq = LineHashSequence.from(content);
        assertNotEquals(seq.getHashForLine(1), seq.getHashForLine(2));
    }

    @Test
    @DisplayName("whitespace normalization: tabs and spaces are equivalent")
    void whitespaceNormalization() {
        // "  hello  world" and "hello\tworld" should produce same hash
        String content1 = "  hello  world\n";
        String content2 = "hello\tworld\n";
        LineHashSequence seq1 = LineHashSequence.from(content1);
        LineHashSequence seq2 = LineHashSequence.from(content2);
        assertEquals(seq1.getHashForLine(1), seq2.getHashForLine(1),
                "Whitespace-normalized lines should produce identical hashes");
    }

    @Test
    @DisplayName("out-of-range line numbers return null")
    void outOfRangeReturnsNull() {
        LineHashSequence seq = LineHashSequence.from("only one line\n");
        assertNull(seq.getHashForLine(0));
        assertNull(seq.getHashForLine(2));
        assertNull(seq.getHashForLine(-1));
    }

    @Test
    @DisplayName("getLinesForHash returns all matching line numbers")
    void getLinesForHashReturnsAllMatches() {
        String content = "dup\nunique\ndup\n";
        LineHashSequence seq = LineHashSequence.from(content);
        String hash = seq.getHashForLine(1);
        var lines = seq.getLinesForHash(hash);
        assertEquals(2, lines.size());
        assertTrue(lines.contains(1));
        assertTrue(lines.contains(3));
    }

    @Test
    @DisplayName("containsHash returns true/false correctly")
    void containsHash() {
        LineHashSequence seq = LineHashSequence.from("hello\n");
        String hash = seq.getHashForLine(1);
        assertTrue(seq.containsHash(hash));
        assertFalse(seq.containsHash("nonexistent_hash"));
    }

    @Test
    @DisplayName("findClosestLineForHash returns closest matching line")
    void findClosestLine() {
        String content = "target\nother\nother2\ntarget\n";
        LineHashSequence seq = LineHashSequence.from(content);
        String hash = seq.getHashForLine(1); // same as line 4

        // Closest to line 1 should return 1
        assertEquals(1, seq.findClosestLineForHash(hash, 1));
        // Closest to line 3 should return 4
        assertEquals(4, seq.findClosestLineForHash(hash, 3));
        // Closest to line 2 should return 1 (distance 1 vs 2)
        assertEquals(1, seq.findClosestLineForHash(hash, 2));
    }

    @Test
    @DisplayName("findClosestLineForHash returns -1 for missing hash")
    void findClosestLineForMissingHash() {
        LineHashSequence seq = LineHashSequence.from("hello\n");
        assertEquals(-1, seq.findClosestLineForHash("missing", 1));
    }

    @Test
    @DisplayName("getContextHash computes hash of surrounding lines")
    void contextHash() {
        String content = "a\nb\nc\nd\ne\n";
        LineHashSequence seq = LineHashSequence.from(content);
        // Context of line 3 with radius 1 = lines 2,3,4
        String ctx1 = seq.getContextHash(3, 1);
        assertNotNull(ctx1);
        // Context of line 3 with radius 2 = lines 1,2,3,4,5
        String ctx2 = seq.getContextHash(3, 2);
        assertNotNull(ctx2);
        // Different radii should produce different context hashes
        assertNotEquals(ctx1, ctx2);
    }

    @Test
    @DisplayName("handles single-line content")
    void singleLine() {
        LineHashSequence seq = LineHashSequence.from("only line");
        assertEquals(1, seq.getLineCount());
        assertNotNull(seq.getHashForLine(1));
    }

    @Test
    @DisplayName("handles empty content")
    void emptyContent() {
        LineHashSequence seq = LineHashSequence.from("");
        assertEquals(0, seq.getLineCount());
    }

    @Test
    @DisplayName("handles null content via empty()")
    void nullContent() {
        LineHashSequence seq = LineHashSequence.empty();
        assertEquals(0, seq.getLineCount());
    }

    @Test
    @DisplayName("blank-only lines produce deterministic hashes")
    void blankLines() {
        String content = "   \n\t\t\n";
        LineHashSequence seq = LineHashSequence.from(content);
        assertEquals(2, seq.getLineCount());
        // Both lines normalize to empty → same hash
        assertEquals(seq.getHashForLine(1), seq.getHashForLine(2));
    }

    @Test
    @DisplayName("hash format is 32-char lowercase hex (MD5)")
    void hashFormat() {
        LineHashSequence seq = LineHashSequence.from("test line\n");
        String hash = seq.getHashForLine(1);
        assertNotNull(hash);
        assertEquals(32, hash.length());
        assertTrue(hash.matches("[0-9a-f]{32}"), "Hash should be lowercase hex");
    }
}
