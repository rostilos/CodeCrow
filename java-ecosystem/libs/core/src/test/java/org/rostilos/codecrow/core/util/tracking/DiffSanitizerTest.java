package org.rostilos.codecrow.core.util.tracking;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.*;

@DisplayName("DiffSanitizer")
class DiffSanitizerTest {

    // ── cleanDiffFormat ──────────────────────────────────────────────────

    @Nested
    @DisplayName("cleanDiffFormat()")
    class CleanDiffFormatTests {

        @Test
        @DisplayName("should return null for null input")
        void shouldReturnNullForNull() {
            assertThat(DiffSanitizer.cleanDiffFormat(null)).isNull();
        }

        @Test
        @DisplayName("should return empty string for empty input")
        void shouldReturnEmptyForEmpty() {
            assertThat(DiffSanitizer.cleanDiffFormat("")).isEmpty();
        }

        @Test
        @DisplayName("should return placeholder unchanged")
        void shouldReturnPlaceholderUnchanged() {
            assertThat(DiffSanitizer.cleanDiffFormat("No suggested fix provided"))
                    .isEqualTo("No suggested fix provided");
        }

        @Test
        @DisplayName("should strip triple-backtick fences")
        void shouldStripTripleBackticks() {
            String input = "```\n--- a/file.java\n+++ b/file.java\n@@ -1,3 +1,3 @@\n- old\n+ new\n```";
            String expected = "--- a/file.java\n+++ b/file.java\n@@ -1,3 +1,3 @@\n- old\n+ new";

            assertThat(DiffSanitizer.cleanDiffFormat(input)).isEqualTo(expected);
        }

        @Test
        @DisplayName("should strip ```diff fences")
        void shouldStripDiffFences() {
            String input = "```diff\n--- a/file.java\n+++ b/file.java\n@@ -1,3 +1,3 @@\n- old\n+ new\n```";
            String expected = "--- a/file.java\n+++ b/file.java\n@@ -1,3 +1,3 @@\n- old\n+ new";

            assertThat(DiffSanitizer.cleanDiffFormat(input)).isEqualTo(expected);
        }

        @Test
        @DisplayName("should strip ```java fences")
        void shouldStripJavaFences() {
            String input = "```java\n--- a/App.java\n+++ b/App.java\n- bad\n+ good\n```";
            String expected = "--- a/App.java\n+++ b/App.java\n- bad\n+ good";

            assertThat(DiffSanitizer.cleanDiffFormat(input)).isEqualTo(expected);
        }

        @Test
        @DisplayName("should not strip legitimate diff content")
        void shouldNotStripLegitimateContent() {
            String input = "--- a/file.java\n+++ b/file.java\n@@ -1,3 +1,3 @@\n- old line\n+ new line";

            assertThat(DiffSanitizer.cleanDiffFormat(input)).isEqualTo(input);
        }

        @Test
        @DisplayName("should handle diff with no fences")
        void shouldHandleDiffWithNoFences() {
            String input = "- removed\n+ added";
            assertThat(DiffSanitizer.cleanDiffFormat(input)).isEqualTo(input);
        }
    }

    // ── isValidDiffFormat ────────────────────────────────────────────────

    @Nested
    @DisplayName("isValidDiffFormat()")
    class IsValidDiffFormatTests {

        @Test
        @DisplayName("should return false for null")
        void shouldReturnFalseForNull() {
            assertThat(DiffSanitizer.isValidDiffFormat(null)).isFalse();
        }

        @Test
        @DisplayName("should return false for blank string")
        void shouldReturnFalseForBlank() {
            assertThat(DiffSanitizer.isValidDiffFormat("   ")).isFalse();
        }

        @Test
        @DisplayName("should return false for placeholder")
        void shouldReturnFalseForPlaceholder() {
            assertThat(DiffSanitizer.isValidDiffFormat("No suggested fix provided")).isFalse();
        }

        @Test
        @DisplayName("should return false for short string")
        void shouldReturnFalseForShort() {
            assertThat(DiffSanitizer.isValidDiffFormat("- a\n+ b")).isFalse();
        }

        @Test
        @DisplayName("should return true for diff with --- marker")
        void shouldReturnTrueForTripleDash() {
            assertThat(DiffSanitizer.isValidDiffFormat("--- a/file.java\n+++ b/file.java\n- old\n+ new"))
                    .isTrue();
        }

        @Test
        @DisplayName("should return true for diff with @@ hunk header")
        void shouldReturnTrueForHunkHeader() {
            assertThat(DiffSanitizer.isValidDiffFormat("@@ -1,3 +1,3 @@\n context\n- old\n+ new"))
                    .isTrue();
        }

        @Test
        @DisplayName("should return true for diff with change lines")
        void shouldReturnTrueForChangeLines() {
            assertThat(DiffSanitizer.isValidDiffFormat("some context line\n-removed line\n+added line"))
                    .isTrue();
        }

        @Test
        @DisplayName("should return false for plain text")
        void shouldReturnFalseForPlainText() {
            assertThat(DiffSanitizer.isValidDiffFormat("This is just a regular description of changes"))
                    .isFalse();
        }
    }

    // ── cleanAndValidate ─────────────────────────────────────────────────

    @Nested
    @DisplayName("cleanAndValidate()")
    class CleanAndValidateTests {

        @Test
        @DisplayName("should return cleaned diff if valid")
        void shouldReturnCleanedIfValid() {
            String input = "```diff\n--- a/file.java\n+++ b/file.java\n@@ -1,3 +1,3 @@\n- old\n+ new\n```";
            String result = DiffSanitizer.cleanAndValidate(input);

            assertThat(result).isNotNull();
            assertThat(result).doesNotContain("```");
            assertThat(result).contains("---");
        }

        @Test
        @DisplayName("should return null if cleaned result is not valid diff")
        void shouldReturnNullIfNotValid() {
            String input = "```\nJust some text that is not a diff at all and is longer than 10 characters\n```";
            assertThat(DiffSanitizer.cleanAndValidate(input)).isNull();
        }

        @Test
        @DisplayName("should return null for null input")
        void shouldReturnNullForNull() {
            assertThat(DiffSanitizer.cleanAndValidate(null)).isNull();
        }
    }
}
