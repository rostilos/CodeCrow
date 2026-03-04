package org.rostilos.codecrow.astparser.model;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class SupportedLanguageTest {

    @ParameterizedTest
    @CsvSource({
            "java, JAVA",
            "py, PYTHON",
            "pyi, PYTHON",
            "js, JAVASCRIPT",
            "mjs, JAVASCRIPT",
            "ts, TYPESCRIPT",
            "tsx, TSX",
            "go, GO",
            "rs, RUST",
            "c, C",
            "h, C",
            "cpp, CPP",
            "hpp, CPP",
            "cs, C_SHARP",
            "php, PHP",
            "rb, RUBY",
            "scala, SCALA",
            "sh, BASH",
            "bash, BASH",
            "hs, HASKELL",
            "css, CSS",
            "html, HTML",
            "json, JSON"
    })
    void fromExtension_resolves_known_extensions(String ext, String expected) {
        Optional<SupportedLanguage> result = SupportedLanguage.fromExtension(ext);
        assertThat(result).isPresent().hasValue(SupportedLanguage.valueOf(expected));
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"txt", "md", "xml", "yaml", "toml", "dockerfile"})
    void fromExtension_returns_empty_for_unsupported(String ext) {
        assertThat(SupportedLanguage.fromExtension(ext)).isEmpty();
    }

    @ParameterizedTest
    @CsvSource({
            "src/main/MyService.java, JAVA",
            "models/user.py, PYTHON",
            "components/App.tsx, TSX",
            "utils/helper.ts, TYPESCRIPT",
            "cmd/main.go, GO",
            "lib/parser.rs, RUST",
            "Program.cs, C_SHARP",
            "scripts/deploy.sh, BASH"
    })
    void fromFilePath_resolves_from_file_path(String path, String expected) {
        assertThat(SupportedLanguage.fromFilePath(path))
                .isPresent()
                .hasValue(SupportedLanguage.valueOf(expected));
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"Dockerfile", "Makefile", "README", ".gitignore"})
    void fromFilePath_returns_empty_for_no_extension(String path) {
        assertThat(SupportedLanguage.fromFilePath(path)).isEmpty();
    }

    @Test
    void isSupported_returns_true_for_supported_files() {
        assertThat(SupportedLanguage.isSupported("Handler.java")).isTrue();
        assertThat(SupportedLanguage.isSupported("test.py")).isTrue();
    }

    @Test
    void isSupported_returns_false_for_unsupported_files() {
        assertThat(SupportedLanguage.isSupported("README.md")).isFalse();
        assertThat(SupportedLanguage.isSupported("Dockerfile")).isFalse();
    }

    @Test
    void each_language_has_scope_query_path() {
        for (SupportedLanguage lang : SupportedLanguage.values()) {
            String path = lang.getScopeQueryPath();
            assertThat(path).startsWith("queries/").endsWith("/scopes.scm");
        }
    }

    @Test
    void each_language_can_create_grammar() {
        // At minimum, JAVA and PYTHON should always work
        assertThat(SupportedLanguage.JAVA.createGrammar()).isNotNull();
        assertThat(SupportedLanguage.PYTHON.createGrammar()).isNotNull();
        assertThat(SupportedLanguage.JAVASCRIPT.createGrammar()).isNotNull();
    }
}
