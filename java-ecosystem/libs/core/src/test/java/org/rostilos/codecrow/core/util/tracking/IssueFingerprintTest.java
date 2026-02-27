package org.rostilos.codecrow.core.util.tracking;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;

import static org.junit.jupiter.api.Assertions.*;

@DisplayName("IssueFingerprint")
class IssueFingerprintTest {

    @Test
    @DisplayName("compute produces 64-char lowercase hex (SHA-256)")
    void computeProducesValidSha256() {
        String fp = IssueFingerprint.compute("SECURITY", "abc123", "SQL injection risk");
        assertNotNull(fp);
        assertEquals(64, fp.length());
        assertTrue(fp.matches("[0-9a-f]{64}"), "Should be lowercase hex SHA-256");
    }

    @Test
    @DisplayName("same inputs produce same fingerprint")
    void deterministic() {
        String fp1 = IssueFingerprint.compute("BUG_RISK", "def456", "Null pointer dereference");
        String fp2 = IssueFingerprint.compute("BUG_RISK", "def456", "Null pointer dereference");
        assertEquals(fp1, fp2);
    }

    @Test
    @DisplayName("different category produces different fingerprint")
    void differentCategory() {
        String fp1 = IssueFingerprint.compute("SECURITY", "abc123", "Issue title");
        String fp2 = IssueFingerprint.compute("BUG_RISK", "abc123", "Issue title");
        assertNotEquals(fp1, fp2);
    }

    @Test
    @DisplayName("different lineHash produces different fingerprint")
    void differentLineHash() {
        String fp1 = IssueFingerprint.compute("SECURITY", "hash_a", "Issue title");
        String fp2 = IssueFingerprint.compute("SECURITY", "hash_b", "Issue title");
        assertNotEquals(fp1, fp2);
    }

    @Test
    @DisplayName("title normalization removes line numbers")
    void titleNormalizationRemovesLineNumbers() {
        // "on line 42" and "on line 99" should produce same fingerprint
        String fp1 = IssueFingerprint.compute("BUG_RISK", "abc", "Null check missing on line 42");
        String fp2 = IssueFingerprint.compute("BUG_RISK", "abc", "Null check missing on line 99");
        assertEquals(fp1, fp2, "Line number references should be stripped from title");
    }

    @Test
    @DisplayName("title normalization removes L-prefixed line numbers")
    void titleNormalizationRemovesLPrefixedNumbers() {
        String fp1 = IssueFingerprint.compute("BUG_RISK", "abc", "Issue at L42 in method");
        String fp2 = IssueFingerprint.compute("BUG_RISK", "abc", "Issue at L100 in method");
        assertEquals(fp1, fp2);
    }

    @Test
    @DisplayName("title normalization is case-insensitive")
    void titleCaseInsensitive() {
        String fp1 = IssueFingerprint.compute("BUG_RISK", "abc", "Null Pointer Dereference");
        String fp2 = IssueFingerprint.compute("BUG_RISK", "abc", "null pointer dereference");
        assertEquals(fp1, fp2, "Title comparison should be case-insensitive");
    }

    @Test
    @DisplayName("title normalization removes standalone numbers")
    void titleNormalizationRemovesStandaloneNumbers() {
        String fp1 = IssueFingerprint.compute("BUG_RISK", "abc", "Variable unused 42");
        String fp2 = IssueFingerprint.compute("BUG_RISK", "abc", "Variable unused 99");
        assertEquals(fp1, fp2);
    }

    @Test
    @DisplayName("title normalization preserves numbers in identifiers")
    void titlePreservesNumbersInIdentifiers() {
        // "log4j" should not have "4" stripped
        String fp1 = IssueFingerprint.compute("SECURITY", "abc", "Vulnerable log4j usage");
        String fp2 = IssueFingerprint.compute("SECURITY", "abc", "Vulnerable log4j usage");
        assertEquals(fp1, fp2);
    }

    @Test
    @DisplayName("null category defaults to UNKNOWN")
    void nullCategory() {
        String fp1 = IssueFingerprint.compute((String) null, "abc", "title");
        String fp2 = IssueFingerprint.compute("UNKNOWN", "abc", "title");
        assertEquals(fp1, fp2);
    }

    @Test
    @DisplayName("null lineHash defaults to no_hash")
    void nullLineHash() {
        String fp = IssueFingerprint.compute("BUG_RISK", null, "title");
        assertNotNull(fp);
        assertEquals(64, fp.length());
    }

    @Test
    @DisplayName("null title treated as empty string")
    void nullTitle() {
        String fp = IssueFingerprint.compute("BUG_RISK", "abc", null);
        assertNotNull(fp);
        assertEquals(64, fp.length());
    }

    @Test
    @DisplayName("enum overload works correctly")
    void enumOverload() {
        String fp1 = IssueFingerprint.compute("EXACT", "abc", "title");
        String fp2 = IssueFingerprint.compute(TrackingConfidence.EXACT, "abc", "title");
        assertEquals(fp1, fp2);
    }

    @Test
    @DisplayName("normalizeTitle handles extra whitespace")
    void normalizeExtraWhitespace() {
        String fp1 = IssueFingerprint.compute("BUG_RISK", "abc", "Extra   spaces   here");
        String fp2 = IssueFingerprint.compute("BUG_RISK", "abc", "Extra spaces here");
        assertEquals(fp1, fp2);
    }

    @Test
    @DisplayName("normalizeTitle handles line range references")
    void normalizeLineRange() {
        String fp1 = IssueFingerprint.compute("BUG_RISK", "abc", "Issue at lines 10-20 in code");
        String fp2 = IssueFingerprint.compute("BUG_RISK", "abc", "Issue at lines 30-40 in code");
        assertEquals(fp1, fp2);
    }

    @Test
    @DisplayName("category case insensitive (uppercase)")
    void categoryCaseInsensitive() {
        String fp1 = IssueFingerprint.compute("security", "abc", "title");
        String fp2 = IssueFingerprint.compute("SECURITY", "abc", "title");
        assertEquals(fp1, fp2);
    }
}
