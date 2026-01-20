package org.rostilos.codecrow.analysisengine.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("DiffContentFilter")
class DiffContentFilterTest {

    @Nested
    @DisplayName("filterDiff() - basic scenarios")
    class FilterDiffBasicTests {

        @Test
        @DisplayName("should return null for null input")
        void shouldReturnNullForNullInput() {
            DiffContentFilter filter = new DiffContentFilter();
            String result = filter.filterDiff(null);
            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return empty string for empty input")
        void shouldReturnEmptyForEmptyInput() {
            DiffContentFilter filter = new DiffContentFilter();
            String result = filter.filterDiff("");
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return unchanged diff if under threshold")
        void shouldReturnUnchangedDiffIfUnderThreshold() {
            DiffContentFilter filter = new DiffContentFilter();
            String smallDiff = """
                diff --git a/test.java b/test.java
                index abc123..def456 100644
                --- a/test.java
                +++ b/test.java
                @@ -1,5 +1,6 @@
                +// small change
                 public class Test {
                 }
                """;

            String result = filter.filterDiff(smallDiff);
            
            assertThat(result).isEqualTo(smallDiff);
        }
    }

    @Nested
    @DisplayName("filterDiff() - large file handling")
    class LargeFileFilteringTests {

        @Test
        @DisplayName("should filter large file diff with placeholder")
        void shouldFilterLargeFileDiff() {
            // Use a small threshold for testing
            DiffContentFilter filter = new DiffContentFilter(100);
            
            String largeDiff = """
                diff --git a/large-file.java b/large-file.java
                index abc123..def456 100644
                --- a/large-file.java
                +++ b/large-file.java
                @@ -1,100 +1,200 @@
                """ + generateLargeContent(200);

            String result = filter.filterDiff(largeDiff);

            assertThat(result).contains("[CodeCrow Filter:");
            assertThat(result).contains("file diff too large");
            assertThat(result).contains("large-file.java");
        }

        @Test
        @DisplayName("should preserve small files and filter only large files")
        void shouldPreserveSmallFilesAndFilterLargeOnes() {
            // Use a threshold that allows the small diff but not the large one
            DiffContentFilter filter = new DiffContentFilter(300);
            
            String mixedDiff = """
                diff --git a/small.java b/small.java
                +// tiny
                diff --git a/large.java b/large.java
                """ + generateLargeContent(100);

            String result = filter.filterDiff(mixedDiff);

            // Small file should be preserved
            assertThat(result).contains("small.java");
            assertThat(result).contains("// tiny");
            // Large file should be filtered
            assertThat(result).contains("[CodeCrow Filter:");
        }

        @Test
        @DisplayName("should include threshold size in placeholder")
        void shouldIncludeThresholdInPlaceholder() {
            int thresholdBytes = 10 * 1024; // 10KB
            DiffContentFilter filter = new DiffContentFilter(thresholdBytes);
            
            String largeDiff = """
                diff --git a/huge.java b/huge.java
                """ + generateLargeContent(500);

            String result = filter.filterDiff(largeDiff);

            assertThat(result).contains("10KB");
        }
    }

    @Nested
    @DisplayName("filterDiff() - change type detection")
    class ChangeTypeDetectionTests {

        @Test
        @DisplayName("should detect and report ADDED change type")
        void shouldDetectAddedChangeType() {
            DiffContentFilter filter = new DiffContentFilter(100);
            
            String diff = """
                diff --git a/new-file.java b/new-file.java
                new file mode 100644
                index 0000000..abc123
                --- /dev/null
                +++ b/new-file.java
                """ + generateLargeContent(50);

            String result = filter.filterDiff(diff);

            assertThat(result).contains("ADDED");
        }

        @Test
        @DisplayName("should detect and report DELETED change type")
        void shouldDetectDeletedChangeType() {
            DiffContentFilter filter = new DiffContentFilter(100);
            
            String diff = """
                diff --git a/old-file.java b/old-file.java
                deleted file mode 100644
                index abc123..0000000
                --- a/old-file.java
                +++ /dev/null
                """ + generateLargeContent(50);

            String result = filter.filterDiff(diff);

            assertThat(result).contains("DELETED");
        }

        @Test
        @DisplayName("should detect and report RENAMED change type")
        void shouldDetectRenamedChangeType() {
            DiffContentFilter filter = new DiffContentFilter(100);
            
            String diff = """
                diff --git a/old-name.java b/new-name.java
                rename from old-name.java
                rename to new-name.java
                similarity index 95%
                """ + generateLargeContent(50);

            String result = filter.filterDiff(diff);

            assertThat(result).contains("RENAMED");
        }

        @Test
        @DisplayName("should detect and report BINARY change type")
        void shouldDetectBinaryChangeType() {
            DiffContentFilter filter = new DiffContentFilter(100);
            
            String diff = """
                diff --git a/image.png b/image.png
                Binary files a/image.png and b/image.png differ
                """ + generateLargeContent(50);

            String result = filter.filterDiff(diff);

            assertThat(result).contains("BINARY");
        }
    }

    @Nested
    @DisplayName("Constructor and thresholds")
    class ThresholdTests {

        @Test
        @DisplayName("should use default threshold of 25KB")
        void shouldUseDefaultThreshold() {
            assertThat(DiffContentFilter.DEFAULT_SIZE_THRESHOLD_BYTES).isEqualTo(25 * 1024);
        }

        @Test
        @DisplayName("should accept custom threshold")
        void shouldAcceptCustomThreshold() {
            DiffContentFilter filter = new DiffContentFilter(50 * 1024); // 50KB
            
            // Create a diff between 25KB and 50KB
            String mediumDiff = "diff --git a/file.java b/file.java\n" + generateLargeContent(1000);
            
            String result = filter.filterDiff(mediumDiff);
            
            // With 50KB threshold, this shouldn't be filtered
            // (the generated content is likely under 50KB)
            assertThat(result).doesNotContain("[CodeCrow Filter:");
        }
    }

    @Nested
    @DisplayName("Edge cases")
    class EdgeCaseTests {

        @Test
        @DisplayName("should handle diff with no proper file markers")
        void shouldHandleDiffWithNoFileMarkers() {
            DiffContentFilter filter = new DiffContentFilter(100);
            
            String invalidDiff = """
                This is not a proper diff format
                just some random text
                that doesn't follow git diff conventions
                """;

            String result = filter.filterDiff(invalidDiff);
            
            // Should return original as-is since it's small and unparseable
            assertThat(result).isEqualTo(invalidDiff);
        }

        @Test
        @DisplayName("should handle diff with Windows line endings")
        void shouldHandleWindowsLineEndings() {
            DiffContentFilter filter = new DiffContentFilter();
            
            String diff = "diff --git a/test.java b/test.java\r\n" +
                         "+// change\r\n" +
                         " public class Test {}\r\n";

            String result = filter.filterDiff(diff);
            
            assertThat(result).isNotNull();
        }
    }

    // Helper method to generate large content
    private static String generateLargeContent(int lines) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < lines; i++) {
            sb.append("+// Line ").append(i).append(": Some content to make the diff larger\n");
        }
        return sb.toString();
    }
}
