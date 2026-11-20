package org.rostilos.codecrow.plugins;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import java.nio.file.Path;
import static org.assertj.core.api.Assertions.assertThat;

class RepositoryFileScopeTest {
    @Test void allExtensionsAreExcludedRegardlessOfCase() {
        for (String extension : RepositoryFileScope.EXTENSIONS) {
            assertThat(RepositoryFileScope.excludes("assets/photo." + extension.toUpperCase(java.util.Locale.ROOT))).isTrue();
            assertThat(RepositoryFileScope.excludes("assets/photo." + extension + ".gz")).isTrue();
            assertThat(RepositoryFileScope.excludes("assets/photo." + extension + ".ts")).isFalse();
            assertThat(RepositoryFileScope.excludes("folder." + extension + "/source.py")).isFalse();
        }
    }

    @Test void sharedFixtureRemovesImageRecordsAndRetainsSource() throws Exception {
        var mapper = new ObjectMapper();
        var fixture = mapper.readTree(Path.of(System.getProperty("codecrow.plugin.fixtures"), "image-exclusion.json").toFile());
        com.fasterxml.jackson.databind.JsonNode actual = mapper.valueToTree(RepositoryFileScope.filterRecords(
                mapper.convertValue(fixture.get("input"), Object.class)));
        assertThat(actual).isEqualTo(fixture.get("expected"));
        String source = "diff --git a/source.py b/source.py\n+image = 'photo.png'\n";
        String image = "diff --git \"a/assets/my logo.SVG\" \"b/assets/my logo.SVG\"\n+<svg/>\n";
        String rename = "diff --git a/logo.PNG b/other.txt\nrename from logo.PNG\nrename to other.txt\n";
        assertThat(RepositoryFileScope.filterDiff(image + source + rename)).isEqualTo(source);
    }

    @Test void spacesInPathsDoNotTurnSourceFilesIntoExcludedFiles() {
        String source = "diff --git a/icon.png helper.py b/icon.png helper.py\r\n+keep\r\n";
        String image = "diff --git a/my logo.svg b/my logo.svg\r\n+<svg/>\r\n";
        assertThat(RepositoryFileScope.filterDiff(source + image)).isEqualTo(source);
    }
}
