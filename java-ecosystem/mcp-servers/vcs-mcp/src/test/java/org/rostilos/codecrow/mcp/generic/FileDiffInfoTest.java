package org.rostilos.codecrow.mcp.generic;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("FileDiffInfo")
class FileDiffInfoTest {

    @Nested
    @DisplayName("Record constructor")
    class RecordConstructor {

        @Test
        @DisplayName("should create with all fields")
        void shouldCreateWithAllFields() {
            FileDiffInfo info = new FileDiffInfo(
                    "src/main/java/Test.java",
                    "MODIFIED",
                    "public class Test {}",
                    "+public class Test {}\n-old code"
            );
            
            assertThat(info.filePath()).isEqualTo("src/main/java/Test.java");
            assertThat(info.diffType()).isEqualTo("MODIFIED");
            assertThat(info.rawContent()).isEqualTo("public class Test {}");
            assertThat(info.diffContent()).isEqualTo("+public class Test {}\n-old code");
        }

        @Test
        @DisplayName("should create with null values")
        void shouldCreateWithNullValues() {
            FileDiffInfo info = new FileDiffInfo(
                    "path/file.txt",
                    null,
                    null,
                    null
            );
            
            assertThat(info.filePath()).isEqualTo("path/file.txt");
            assertThat(info.diffType()).isNull();
            assertThat(info.rawContent()).isNull();
            assertThat(info.diffContent()).isNull();
        }
    }

    @Nested
    @DisplayName("Record accessors")
    class RecordAccessors {

        @Test
        @DisplayName("filePath() should return file path")
        void filePathShouldReturnFilePath() {
            FileDiffInfo info = new FileDiffInfo("a/b/c.java", "ADDED", "", "");
            assertThat(info.filePath()).isEqualTo("a/b/c.java");
        }

        @Test
        @DisplayName("diffType() should return diff type")
        void diffTypeShouldReturnDiffType() {
            FileDiffInfo added = new FileDiffInfo("f", "ADDED", "", "");
            FileDiffInfo deleted = new FileDiffInfo("f", "DELETED", "", "");
            FileDiffInfo modified = new FileDiffInfo("f", "MODIFIED", "", "");
            
            assertThat(added.diffType()).isEqualTo("ADDED");
            assertThat(deleted.diffType()).isEqualTo("DELETED");
            assertThat(modified.diffType()).isEqualTo("MODIFIED");
        }
    }

    @Nested
    @DisplayName("Equality")
    class EqualityTests {

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            FileDiffInfo info1 = new FileDiffInfo("path", "type", "raw", "diff");
            FileDiffInfo info2 = new FileDiffInfo("path", "type", "raw", "diff");
            
            assertThat(info1).isEqualTo(info2);
            assertThat(info1.hashCode()).isEqualTo(info2.hashCode());
        }

        @Test
        @DisplayName("should not be equal for different values")
        void shouldNotBeEqualForDifferentValues() {
            FileDiffInfo info1 = new FileDiffInfo("path1", "type", "raw", "diff");
            FileDiffInfo info2 = new FileDiffInfo("path2", "type", "raw", "diff");
            
            assertThat(info1).isNotEqualTo(info2);
        }
    }
}
