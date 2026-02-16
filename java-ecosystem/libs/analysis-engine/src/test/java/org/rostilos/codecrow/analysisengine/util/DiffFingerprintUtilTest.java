package org.rostilos.codecrow.analysisengine.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("DiffFingerprintUtil")
class DiffFingerprintUtilTest {

    @Nested
    @DisplayName("compute()")
    class ComputeTests {

        @Test void returnsNull_forNull() {
            assertThat(DiffFingerprintUtil.compute(null)).isNull();
        }

        @Test void returnsNull_forEmpty() {
            assertThat(DiffFingerprintUtil.compute("")).isNull();
        }

        @Test void returnsNull_forBlank() {
            assertThat(DiffFingerprintUtil.compute("   \n  \t  ")).isNull();
        }

        @Test void returnsNull_forContextOnlyDiff() {
            String diff = """
                    diff --git a/file.java b/file.java
                     context line 1
                     context line 2
                    """;
            assertThat(DiffFingerprintUtil.compute(diff)).isNull();
        }

        @Test void returns64CharHex_forValidDiff() {
            String diff = """
                    diff --git a/file.java b/file.java
                    +added line
                    -removed line
                    """;
            String fingerprint = DiffFingerprintUtil.compute(diff);
            assertThat(fingerprint).isNotNull();
            assertThat(fingerprint).hasSize(64);
            assertThat(fingerprint).matches("[0-9a-f]{64}");
        }

        @Test void excludes_fileHeaders() {
            // +++ and --- lines should be excluded
            String diff = """
                    --- a/file.java
                    +++ b/file.java
                    +real change
                    """;
            String fingerprint = DiffFingerprintUtil.compute(diff);
            assertThat(fingerprint).isNotNull();
        }

        @Test void excludes_diffHeaders() {
            // "diff " lines should be excluded
            String diff = """
                    diff --git a/file.java b/file.java
                    +added content
                    """;
            String fingerprint = DiffFingerprintUtil.compute(diff);
            assertThat(fingerprint).isNotNull();
        }

        @Test void sameContent_sameFingerprintRegardlessOfOrder() {
            String diff1 = """
                    diff --git a/a.java b/a.java
                    +line A
                    diff --git a/b.java b/b.java
                    +line B
                    """;
            String diff2 = """
                    diff --git a/b.java b/b.java
                    +line B
                    diff --git a/a.java b/a.java
                    +line A
                    """;
            // Because change lines are sorted, order shouldn't matter
            assertThat(DiffFingerprintUtil.compute(diff1))
                    .isEqualTo(DiffFingerprintUtil.compute(diff2));
        }

        @Test void differentContent_differentFingerprint() {
            String diff1 = "+line X\n-line Y\n";
            String diff2 = "+line A\n-line B\n";
            assertThat(DiffFingerprintUtil.compute(diff1))
                    .isNotEqualTo(DiffFingerprintUtil.compute(diff2));
        }

        @Test void handlesWindowsLineEndings() {
            String diff = "+added line\r\n-removed line\r\n";
            String fingerprint = DiffFingerprintUtil.compute(diff);
            assertThat(fingerprint).isNotNull().hasSize(64);
        }

        @Test void trimTrailingWhitespace() {
            String diff1 = "+line with trailing spaces   \n";
            String diff2 = "+line with trailing spaces\n";
            assertThat(DiffFingerprintUtil.compute(diff1))
                    .isEqualTo(DiffFingerprintUtil.compute(diff2));
        }
    }
}
