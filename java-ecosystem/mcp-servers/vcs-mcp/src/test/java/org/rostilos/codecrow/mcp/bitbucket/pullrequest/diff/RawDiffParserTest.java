package org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("RawDiffParser")
class RawDiffParserTest {

    private RawDiffParser parser;

    @BeforeEach
    void setUp() {
        parser = new RawDiffParser();
    }

    @Nested
    @DisplayName("execute() - Basic Parsing")
    class BasicParsing {

        @Test
        @DisplayName("should parse single added file")
        void shouldParseSingleAddedFile() {
            String rawDiff = """
                    diff --git a/src/NewFile.java b/src/NewFile.java
                    new file mode 100644
                    index 0000000..abc1234
                    --- /dev/null
                    +++ b/src/NewFile.java
                    @@ -0,0 +1,5 @@
                    +public class NewFile {
                    +    public void test() {
                    +        System.out.println("Hello");
                    +    }
                    +}
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getFilePath()).isEqualTo("src/NewFile.java");
            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.ADDED);
        }

        @Test
        @DisplayName("should parse single modified file")
        void shouldParseSingleModifiedFile() {
            String rawDiff = """
                    diff --git a/src/ExistingFile.java b/src/ExistingFile.java
                    index abc1234..def5678 100644
                    --- a/src/ExistingFile.java
                    +++ b/src/ExistingFile.java
                    @@ -1,3 +1,4 @@
                     public class ExistingFile {
                    +    private int count;
                         public void test() {
                         }
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getFilePath()).isEqualTo("src/ExistingFile.java");
            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.MODIFIED);
        }

        @Test
        @DisplayName("should parse single deleted file")
        void shouldParseSingleDeletedFile() {
            String rawDiff = """
                    diff --git a/src/OldFile.java b/src/OldFile.java
                    deleted file mode 100644
                    index abc1234..0000000
                    --- a/src/OldFile.java
                    +++ /dev/null
                    @@ -1,5 +0,0 @@
                    -public class OldFile {
                    -    public void test() {
                    -        System.out.println("Goodbye");
                    -    }
                    -}
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getFilePath()).isEqualTo("src/OldFile.java");
            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.REMOVED);
        }
    }

    @Nested
    @DisplayName("execute() - Multiple Files")
    class MultipleFiles {

        @Test
        @DisplayName("should parse multiple files with different diff types")
        void shouldParseMultipleFilesWithDifferentTypes() {
            String rawDiff = """
                    diff --git a/src/NewFile.java b/src/NewFile.java
                    new file mode 100644
                    index 0000000..abc1234
                    --- /dev/null
                    +++ b/src/NewFile.java
                    @@ -0,0 +1,3 @@
                    +public class NewFile {
                    +}
                    diff --git a/src/ModifiedFile.java b/src/ModifiedFile.java
                    index abc1234..def5678 100644
                    --- a/src/ModifiedFile.java
                    +++ b/src/ModifiedFile.java
                    @@ -1,2 +1,3 @@
                     class ModifiedFile {
                    +    int x;
                     }
                    diff --git a/src/DeletedFile.java b/src/DeletedFile.java
                    deleted file mode 100644
                    index xyz..000 100644
                    --- a/src/DeletedFile.java
                    +++ /dev/null
                    @@ -1,2 +0,0 @@
                    -class DeletedFile {
                    -}
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(3);

            assertThat(result.get(0).getFilePath()).isEqualTo("src/NewFile.java");
            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.ADDED);

            assertThat(result.get(1).getFilePath()).isEqualTo("src/ModifiedFile.java");
            assertThat(result.get(1).getDiffType()).isEqualTo(FileDiff.DiffType.MODIFIED);

            assertThat(result.get(2).getFilePath()).isEqualTo("src/DeletedFile.java");
            assertThat(result.get(2).getDiffType()).isEqualTo(FileDiff.DiffType.REMOVED);
        }

        @Test
        @DisplayName("should preserve file order")
        void shouldPreserveFileOrder() {
            String rawDiff = """
                    diff --git a/z-file.java b/z-file.java
                    index abc..def 100644
                    --- a/z-file.java
                    +++ b/z-file.java
                    @@ -1 +1 @@
                    -old
                    +new
                    diff --git a/a-file.java b/a-file.java
                    index 123..456 100644
                    --- a/a-file.java
                    +++ b/a-file.java
                    @@ -1 +1 @@
                    -old
                    +new
                    diff --git a/m-file.java b/m-file.java
                    index 789..012 100644
                    --- a/m-file.java
                    +++ b/m-file.java
                    @@ -1 +1 @@
                    -old
                    +new
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(3);
            assertThat(result.get(0).getFilePath()).isEqualTo("z-file.java");
            assertThat(result.get(1).getFilePath()).isEqualTo("a-file.java");
            assertThat(result.get(2).getFilePath()).isEqualTo("m-file.java");
        }
    }

    @Nested
    @DisplayName("execute() - File Paths")
    class FilePaths {

        @Test
        @DisplayName("should handle nested directory paths")
        void shouldHandleNestedDirectoryPaths() {
            String rawDiff = """
                    diff --git a/src/main/java/com/example/service/MyService.java b/src/main/java/com/example/service/MyService.java
                    index abc..def 100644
                    --- a/src/main/java/com/example/service/MyService.java
                    +++ b/src/main/java/com/example/service/MyService.java
                    @@ -1 +1 @@
                    -old
                    +new
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getFilePath()).isEqualTo("src/main/java/com/example/service/MyService.java");
        }

        @Test
        @DisplayName("should handle files in root directory")
        void shouldHandleFilesInRootDirectory() {
            String rawDiff = """
                    diff --git a/README.md b/README.md
                    index abc..def 100644
                    --- a/README.md
                    +++ b/README.md
                    @@ -1 +1 @@
                    -old
                    +new
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getFilePath()).isEqualTo("README.md");
        }

        @Test
        @DisplayName("should handle files with special characters in path")
        void shouldHandleFilesWithSpecialCharactersInPath() {
            String rawDiff = """
                    diff --git a/src/test-file_v2.java b/src/test-file_v2.java
                    index abc..def 100644
                    --- a/src/test-file_v2.java
                    +++ b/src/test-file_v2.java
                    @@ -1 +1 @@
                    -old
                    +new
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getFilePath()).isEqualTo("src/test-file_v2.java");
        }
    }

    @Nested
    @DisplayName("execute() - Changes Content")
    class ChangesContent {

        @Test
        @DisplayName("should capture changes content including headers")
        void shouldCaptureChangesContentIncludingHeaders() {
            String rawDiff = """
                    diff --git a/src/File.java b/src/File.java
                    index abc..def 100644
                    --- a/src/File.java
                    +++ b/src/File.java
                    @@ -1,3 +1,4 @@
                     class File {
                    +    int x;
                     }
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getChanges()).isNotEmpty();
            assertThat(result.get(0).getChanges()).contains("diff --git");
            assertThat(result.get(0).getChanges()).contains("@@");
            assertThat(result.get(0).getChanges()).contains("+    int x;");
        }

        @Test
        @DisplayName("should handle multiple hunks in single file")
        void shouldHandleMultipleHunksInSingleFile() {
            String rawDiff = """
                    diff --git a/src/File.java b/src/File.java
                    index abc..def 100644
                    --- a/src/File.java
                    +++ b/src/File.java
                    @@ -1,3 +1,4 @@
                     class File {
                    +    int x;
                     }
                    @@ -10,3 +11,4 @@
                     void method() {
                    +    System.out.println("test");
                     }
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            String changes = result.get(0).getChanges();
            // Should contain both hunks
            assertThat(changes).contains("@@ -1,3 +1,4 @@");
            assertThat(changes).contains("@@ -10,3 +11,4 @@");
        }
    }

    @Nested
    @DisplayName("execute() - Edge Cases")
    class EdgeCases {

        @Test
        @DisplayName("should return empty list for empty input")
        void shouldReturnEmptyListForEmptyInput() {
            List<FileDiff> result = parser.execute("");

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return empty list for whitespace only input")
        void shouldReturnEmptyListForWhitespaceOnlyInput() {
            List<FileDiff> result = parser.execute("   \n\n\t  ");

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return empty list for non-diff content")
        void shouldReturnEmptyListForNonDiffContent() {
            String nonDiff = """
                    This is not a diff file.
                    It's just random text.
                    Nothing to see here.
                    """;

            List<FileDiff> result = parser.execute(nonDiff);

            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should skip index lines")
        void shouldSkipIndexLines() {
            String rawDiff = """
                    diff --git a/src/File.java b/src/File.java
                    index abc1234def5678..123456789abcdef 100644
                    --- a/src/File.java
                    +++ b/src/File.java
                    @@ -1 +1 @@
                    -old
                    +new
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            // The changes should not contain the index line
            assertThat(result.get(0).getChanges()).doesNotContain("index abc1234def5678");
        }

        @Test
        @DisplayName("should handle diff with no hunk header")
        void shouldHandleDiffWithNoHunkHeader() {
            String rawDiff = """
                    diff --git a/src/Empty.java b/src/Empty.java
                    new file mode 100644
                    --- /dev/null
                    +++ b/src/Empty.java
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getFilePath()).isEqualTo("src/Empty.java");
            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.ADDED);
        }
    }

    @Nested
    @DisplayName("getDiffTypeFromFileLine() - Type Detection")
    class DiffTypeDetection {

        @Test
        @DisplayName("should detect ADDED from 'new file mode' line")
        void shouldDetectAddedFromNewFileMode() {
            String rawDiff = """
                    diff --git a/src/New.java b/src/New.java
                    new file mode 100644
                    --- /dev/null
                    +++ b/src/New.java
                    @@ -0,0 +1 @@
                    +content
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.ADDED);
        }

        @Test
        @DisplayName("should detect REMOVED from 'deleted file mode' line")
        void shouldDetectRemovedFromDeletedFileMode() {
            String rawDiff = """
                    diff --git a/src/Old.java b/src/Old.java
                    deleted file mode 100644
                    --- a/src/Old.java
                    +++ /dev/null
                    @@ -1 +0,0 @@
                    -content
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.REMOVED);
        }

        @Test
        @DisplayName("should detect ADDED from /dev/null in old file")
        void shouldDetectAddedFromDevNullInOldFile() {
            String rawDiff = """
                    diff --git a/src/New.java b/src/New.java
                    --- /dev/null
                    +++ b/src/New.java
                    @@ -0,0 +1 @@
                    +content
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.ADDED);
        }

        @Test
        @DisplayName("should detect MODIFIED from standard --- line")
        void shouldDetectModifiedFromStandardLine() {
            String rawDiff = """
                    diff --git a/src/Mod.java b/src/Mod.java
                    --- a/src/Mod.java
                    +++ b/src/Mod.java
                    @@ -1 +1 @@
                    -old
                    +new
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.MODIFIED);
        }
    }

    @Nested
    @DisplayName("execute() - Binary Files")
    class BinaryFiles {

        @Test
        @DisplayName("should handle binary file additions")
        void shouldHandleBinaryFileAdditions() {
            String rawDiff = """
                    diff --git a/src/image.png b/src/image.png
                    new file mode 100644
                    Binary files /dev/null and b/src/image.png differ
                    """;

            List<FileDiff> result = parser.execute(rawDiff);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getFilePath()).isEqualTo("src/image.png");
            assertThat(result.get(0).getDiffType()).isEqualTo(FileDiff.DiffType.ADDED);
        }
    }
}
