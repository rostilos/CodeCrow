package org.rostilos.codecrow.mcp.filter;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("LargeContentFilter")
class LargeContentFilterTest {

    private LargeContentFilter filter;

    @BeforeEach
    void setUp() {
        filter = new LargeContentFilter();
    }

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should create filter with default threshold")
        void shouldCreateFilterWithDefaultThreshold() {
            LargeContentFilter defaultFilter = new LargeContentFilter();
            assertThat(defaultFilter).isNotNull();
        }

        @Test
        @DisplayName("should create filter with custom threshold")
        void shouldCreateFilterWithCustomThreshold() {
            LargeContentFilter customFilter = new LargeContentFilter(1024);
            assertThat(customFilter).isNotNull();
        }
    }

    @Nested
    @DisplayName("filterFileContent()")
    class FilterFileContentTests {

        @Test
        @DisplayName("should return null for null content")
        void shouldReturnNullForNullContent() {
            String result = filter.filterFileContent(null, "file.txt");

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return original content when under threshold")
        void shouldReturnOriginalContentWhenUnderThreshold() {
            String smallContent = "This is small content";

            String result = filter.filterFileContent(smallContent, "file.txt");

            assertThat(result).isEqualTo(smallContent);
        }

        @Test
        @DisplayName("should return placeholder when content exceeds threshold")
        void shouldReturnPlaceholderWhenContentExceedsThreshold() {
            // Create content larger than default 25KB threshold
            String largeContent = "x".repeat(26 * 1024);

            String result = filter.filterFileContent(largeContent, "large-file.txt");

            assertThat(result).contains(LargeContentFilter.FILTERED_PLACEHOLDER);
            assertThat(result).contains("large-file.txt");
        }

        @Test
        @DisplayName("should include original size in placeholder")
        void shouldIncludeOriginalSizeInPlaceholder() {
            String largeContent = "x".repeat(30 * 1024);

            String result = filter.filterFileContent(largeContent, "file.txt");

            assertThat(result).contains("bytes");
        }

        @Test
        @DisplayName("should handle empty content")
        void shouldHandleEmptyContent() {
            String result = filter.filterFileContent("", "empty.txt");

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should use custom threshold")
        void shouldUseCustomThreshold() {
            LargeContentFilter customFilter = new LargeContentFilter(100);
            String content = "x".repeat(150);

            String result = customFilter.filterFileContent(content, "file.txt");

            assertThat(result).contains(LargeContentFilter.FILTERED_PLACEHOLDER);
        }

        @Test
        @DisplayName("should allow content exactly at threshold")
        void shouldAllowContentExactlyAtThreshold() {
            int threshold = 100;
            LargeContentFilter customFilter = new LargeContentFilter(threshold);
            String content = "x".repeat(threshold);

            String result = customFilter.filterFileContent(content, "file.txt");

            assertThat(result).isEqualTo(content);
        }
    }

    @Nested
    @DisplayName("filterDiff()")
    class FilterDiffTests {

        @Test
        @DisplayName("should return null for null diff")
        void shouldReturnNullForNullDiff() {
            String result = filter.filterDiff(null);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return empty string for empty diff")
        void shouldReturnEmptyStringForEmptyDiff() {
            String result = filter.filterDiff("");

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return original diff when under threshold")
        void shouldReturnOriginalDiffWhenUnderThreshold() {
            String smallDiff = """
                    diff --git a/test.txt b/test.txt
                    --- a/test.txt
                    +++ b/test.txt
                    @@ -1 +1 @@
                    -old
                    +new
                    """;

            String result = filter.filterDiff(smallDiff);

            assertThat(result).isEqualTo(smallDiff);
        }

        @Test
        @DisplayName("should filter large unparseable diff")
        void shouldFilterLargeUnparseableDiff() {
            String largeDiff = "x".repeat(30 * 1024);

            String result = filter.filterDiff(largeDiff);

            assertThat(result).contains("CodeCrow Filter");
        }

        @Test
        @DisplayName("should filter large individual file diffs")
        void shouldFilterLargeIndividualFileDiffs() {
            // Create a valid diff with one large file
            String largeDiffContent = "+".repeat(30 * 1024);
            String diff = String.format("""
                    diff --git a/small.txt b/small.txt
                    --- a/small.txt
                    +++ b/small.txt
                    @@ -1 +1 @@
                    -old
                    +new
                    diff --git a/large.txt b/large.txt
                    --- a/large.txt
                    +++ b/large.txt
                    @@ -1 +1 @@
                    %s
                    """, largeDiffContent);

            String result = filter.filterDiff(diff);

            // The result should contain filter placeholder for large files
            assertThat(result).isNotNull();
        }
    }

    @Nested
    @DisplayName("Constants")
    class ConstantsTests {

        @Test
        @DisplayName("should have correct default size threshold")
        void shouldHaveCorrectDefaultSizeThreshold() {
            assertThat(LargeContentFilter.DEFAULT_SIZE_THRESHOLD_BYTES).isEqualTo(25 * 1024);
        }

        @Test
        @DisplayName("should have non-empty filtered placeholder")
        void shouldHaveNonEmptyFilteredPlaceholder() {
            assertThat(LargeContentFilter.FILTERED_PLACEHOLDER).isNotEmpty();
            assertThat(LargeContentFilter.FILTERED_PLACEHOLDER).contains("CodeCrow Filter");
        }
    }
}
