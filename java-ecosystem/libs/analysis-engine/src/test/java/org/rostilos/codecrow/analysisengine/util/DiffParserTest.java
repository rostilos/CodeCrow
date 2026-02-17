package org.rostilos.codecrow.analysisengine.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.util.DiffParser.DiffFileInfo;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("DiffParser")
class DiffParserTest {

    @Nested
    @DisplayName("parseDiff()")
    class ParseDiffTests {

        @Test
        @DisplayName("should return empty list for null diff")
        void shouldReturnEmptyForNullDiff() {
            List<DiffFileInfo> result = DiffParser.parseDiff(null, 3);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should return empty list for blank diff")
        void shouldReturnEmptyForBlankDiff() {
            List<DiffFileInfo> result = DiffParser.parseDiff("   ", 3);
            assertThat(result).isEmpty();
        }

        @Test
        @DisplayName("should parse single file diff")
        void shouldParseSingleFileDiff() {
            String diff = """
                    diff --git a/src/main/java/Test.java b/src/main/java/Test.java
                    index abc123..def456 100644
                    --- a/src/main/java/Test.java
                    +++ b/src/main/java/Test.java
                    @@ -1,5 +1,6 @@
                     package com.example;

                    +import java.util.List;
                    +
                     public class Test {
                         public void test() {
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getPath()).isEqualTo("src/main/java/Test.java");
            assertThat(result.get(0).getChangeType()).isEqualTo("modified");
        }

        @Test
        @DisplayName("should parse multiple file diffs")
        void shouldParseMultipleFileDiffs() {
            String diff = """
                    diff --git a/src/main/java/First.java b/src/main/java/First.java
                    index abc123..def456 100644
                    --- a/src/main/java/First.java
                    +++ b/src/main/java/First.java
                    @@ -1,5 +1,6 @@
                    +import java.util.List;
                     public class First {
                     }
                    diff --git a/src/main/java/Second.java b/src/main/java/Second.java
                    index abc123..def456 100644
                    --- a/src/main/java/Second.java
                    +++ b/src/main/java/Second.java
                    @@ -1,5 +1,6 @@
                    +import java.util.Map;
                     public class Second {
                     }
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result).hasSize(2);
            assertThat(result.get(0).getPath()).isEqualTo("src/main/java/First.java");
            assertThat(result.get(1).getPath()).isEqualTo("src/main/java/Second.java");
        }

        @Test
        @DisplayName("should detect new file mode")
        void shouldDetectNewFileMode() {
            String diff = """
                    diff --git a/src/NewFile.java b/src/NewFile.java
                    new file mode 100644
                    index 0000000..abc123
                    --- /dev/null
                    +++ b/src/NewFile.java
                    @@ -0,0 +1,5 @@
                    +public class NewFile {
                    +}
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getChangeType()).isEqualTo("added");
        }

        @Test
        @DisplayName("should detect deleted file mode")
        void shouldDetectDeletedFileMode() {
            String diff = """
                    diff --git a/src/OldFile.java b/src/OldFile.java
                    deleted file mode 100644
                    index abc123..0000000
                    --- a/src/OldFile.java
                    +++ /dev/null
                    @@ -1,5 +0,0 @@
                    -public class OldFile {
                    -}
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getChangeType()).isEqualTo("deleted");
        }

        @Test
        @DisplayName("should detect renamed file")
        void shouldDetectRenamedFile() {
            String diff = """
                    diff --git a/src/OldName.java b/src/NewName.java
                    similarity index 95%
                    rename from src/OldName.java
                    rename to src/NewName.java
                    index abc123..def456 100644
                    --- a/src/OldName.java
                    +++ b/src/NewName.java
                    @@ -1,5 +1,5 @@
                     public class NewName {
                     }
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getChangeType()).isEqualTo("renamed");
        }

        @Test
        @DisplayName("should extract code snippets from added lines")
        void shouldExtractCodeSnippets() {
            String diff = """
                    diff --git a/src/Service.java b/src/Service.java
                    index abc123..def456 100644
                    --- a/src/Service.java
                    +++ b/src/Service.java
                    @@ -1,5 +1,10 @@
                     public class Service {
                    +    public void processData(String input) {
                    +        // Process the input
                    +        System.out.println(input);
                    +    }
                     }
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result).hasSize(1);
            assertThat(result.get(0).getCodeSnippets()).isNotEmpty();
            // Should prioritize function signature
            assertThat(result.get(0).getCodeSnippets().get(0)).contains("processData");
        }
    }

    @Nested
    @DisplayName("extractChangedFiles()")
    class ExtractChangedFilesTests {

        @Test
        @DisplayName("should extract only non-deleted file paths")
        void shouldExtractOnlyNonDeletedFiles() {
            String diff = """
                    diff --git a/src/Keep.java b/src/Keep.java
                    index abc123..def456 100644
                    --- a/src/Keep.java
                    +++ b/src/Keep.java
                    +// change
                    diff --git a/src/Delete.java b/src/Delete.java
                    deleted file mode 100644
                    index abc123..0000000
                    --- a/src/Delete.java
                    +++ /dev/null
                    """;

            List<String> files = DiffParser.extractChangedFiles(diff);

            assertThat(files).containsExactly("src/Keep.java");
            assertThat(files).doesNotContain("src/Delete.java");
        }

        @Test
        @DisplayName("should return empty list for null diff")
        void shouldReturnEmptyForNullDiff() {
            List<String> files = DiffParser.extractChangedFiles(null);
            assertThat(files).isEmpty();
        }
    }

    @Nested
    @DisplayName("extractDeletedFiles()")
    class ExtractDeletedFilesTests {

        @Test
        @DisplayName("should extract only deleted file paths")
        void shouldExtractOnlyDeletedFiles() {
            String diff = """
                    diff --git a/src/Keep.java b/src/Keep.java
                    index abc123..def456 100644
                    --- a/src/Keep.java
                    +++ b/src/Keep.java
                    +// change
                    diff --git a/src/Delete.java b/src/Delete.java
                    deleted file mode 100644
                    index abc123..0000000
                    --- a/src/Delete.java
                    +++ /dev/null
                    diff --git a/src/AlsoDelete.java b/src/AlsoDelete.java
                    deleted file mode 100644
                    index abc123..0000000
                    --- a/src/AlsoDelete.java
                    +++ /dev/null
                    """;

            List<String> files = DiffParser.extractDeletedFiles(diff);

            assertThat(files).containsExactly("src/Delete.java", "src/AlsoDelete.java");
            assertThat(files).doesNotContain("src/Keep.java");
        }

        @Test
        @DisplayName("should return empty list when no files are deleted")
        void shouldReturnEmptyWhenNoDeleted() {
            String diff = """
                    diff --git a/src/Modified.java b/src/Modified.java
                    index abc123..def456 100644
                    --- a/src/Modified.java
                    +++ b/src/Modified.java
                    +// change
                    """;

            List<String> files = DiffParser.extractDeletedFiles(diff);
            assertThat(files).isEmpty();
        }

        @Test
        @DisplayName("should return empty list for null diff")
        void shouldReturnEmptyForNullDiff() {
            List<String> files = DiffParser.extractDeletedFiles(null);
            assertThat(files).isEmpty();
        }
    }

    @Nested
    @DisplayName("extractDiffSnippets()")
    class ExtractDiffSnippetsTests {

        @Test
        @DisplayName("should extract snippets up to max limit")
        void shouldExtractSnippetsUpToMaxLimit() {
            String diff = """
                    diff --git a/src/A.java b/src/A.java
                    +public void methodA() {}
                    +public void methodB() {}
                    +public void methodC() {}
                    diff --git a/src/B.java b/src/B.java
                    +public void methodD() {}
                    +public void methodE() {}
                    """;

            List<String> snippets = DiffParser.extractDiffSnippets(diff, 2);

            assertThat(snippets).hasSize(2);
        }

        @Test
        @DisplayName("should return empty list for null diff")
        void shouldReturnEmptyForNullDiff() {
            List<String> snippets = DiffParser.extractDiffSnippets(null, 5);
            assertThat(snippets).isEmpty();
        }
    }

    @Nested
    @DisplayName("Significant line detection")
    class SignificantLineTests {

        @Test
        @DisplayName("should detect Java method signatures")
        void shouldDetectJavaMethodSignatures() {
            String diff = """
                    diff --git a/Test.java b/Test.java
                    +public void processRequest(String data) {
                    +    return data;
                    +}
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result.get(0).getCodeSnippets()).isNotEmpty();
            assertThat(result.get(0).getCodeSnippets().get(0)).contains("processRequest");
        }

        @Test
        @DisplayName("should detect Python function definitions")
        void shouldDetectPythonFunctions() {
            String diff = """
                    diff --git a/test.py b/test.py
                    +def calculate_total(items):
                    +    return sum(items)
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result.get(0).getCodeSnippets()).isNotEmpty();
            assertThat(result.get(0).getCodeSnippets().get(0)).contains("calculate_total");
        }

        @Test
        @DisplayName("should detect JavaScript functions")
        void shouldDetectJavaScriptFunctions() {
            String diff = """
                    diff --git a/test.js b/test.js
                    +function processData(input) {
                    +    return input.trim();
                    +}
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result.get(0).getCodeSnippets()).isNotEmpty();
            assertThat(result.get(0).getCodeSnippets().get(0)).contains("processData");
        }

        @Test
        @DisplayName("should detect class definitions")
        void shouldDetectClassDefinitions() {
            String diff = """
                    diff --git a/Test.java b/Test.java
                    +class UserService {
                    +    // service implementation
                    +}
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 3);

            assertThat(result.get(0).getCodeSnippets()).isNotEmpty();
            assertThat(result.get(0).getCodeSnippets().get(0)).contains("UserService");
        }

        @Test
        @DisplayName("should skip comments when looking for snippets")
        void shouldSkipComments() {
            String diff = """
                    diff --git a/Test.java b/Test.java
                    +// This is a comment
                    +# This is another comment
                    +public void actualMethod() {}
                    """;

            List<DiffFileInfo> result = DiffParser.parseDiff(diff, 1);

            assertThat(result.get(0).getCodeSnippets()).hasSize(1);
            assertThat(result.get(0).getCodeSnippets().get(0)).contains("actualMethod");
        }
    }

    @Nested
    @DisplayName("DiffFileInfo")
    class DiffFileInfoTests {

        @Test
        @DisplayName("should store all properties correctly")
        void shouldStoreAllPropertiesCorrectly() {
            List<String> snippets = List.of("snippet1", "snippet2");
            DiffFileInfo info = new DiffFileInfo("path/to/file.java", "modified", snippets);

            assertThat(info.getPath()).isEqualTo("path/to/file.java");
            assertThat(info.getChangeType()).isEqualTo("modified");
            assertThat(info.getCodeSnippets()).containsExactly("snippet1", "snippet2");
        }
    }
}
