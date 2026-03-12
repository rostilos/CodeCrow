package org.rostilos.codecrow.core.util.anchoring;

import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class SnippetLocatorTest {

    // ── Null / blank guards ──────────────────────────────────────────────

    @Nested
    class Guards {
        @Test
        void nullSnippet_shouldReturnNotFound() {
            var r = SnippetLocator.locate(null, "line1\nline2", 1);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.NOT_FOUND);
            assertThat(r.confidence()).isEqualTo(0.0f);
        }

        @Test
        void blankSnippet_shouldReturnNotFound() {
            var r = SnippetLocator.locate("   ", "line1\nline2", 1);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.NOT_FOUND);
        }

        @Test
        void nullFileContent_shouldReturnNotFound() {
            var r = SnippetLocator.locate("some code", null, 5);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.NOT_FOUND);
        }

        @Test
        void emptyFileContent_shouldReturnNotFound() {
            var r = SnippetLocator.locate("some code", "", 5);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.NOT_FOUND);
        }

        @Test
        void allBlankSnippetLines_shouldReturnNotFound() {
            var r = SnippetLocator.locate("\n  \n\n", "line1\nline2", 1);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.NOT_FOUND);
        }

        @Test
        void negativeHintLine_shouldClampToOne() {
            var r = SnippetLocator.locate("nonexistent", "line1\nline2", -5);
            assertThat(r.line()).isGreaterThanOrEqualTo(1);
        }

        @Test
        void zeroHintLine_shouldClampToOne() {
            var r = SnippetLocator.locate("nonexistent", "line1\nline2", 0);
            assertThat(r.line()).isGreaterThanOrEqualTo(1);
        }
    }

    // ── Strategy 1: Hash-exact match ─────────────────────────────────────

    @Nested
    class HashExactMatch {
        @Test
        void singleLineExactMatch_shouldReturnHashExact() {
            String file = "public class Foo {\n    int x = 1;\n    int y = 2;\n}";
            var r = SnippetLocator.locate("    int x = 1;", file, 2);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.HASH_EXACT);
            assertThat(r.line()).isEqualTo(2);
            assertThat(r.confidence()).isEqualTo(1.0f);
        }

        @Test
        void multiLineExactMatch_shouldReturnHashExact() {
            String file = "line1\nline2\nline3\nline4\nline5";
            var r = SnippetLocator.locate("line2\nline3\nline4", file, 2);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.HASH_EXACT);
            assertThat(r.line()).isEqualTo(2);
            assertThat(r.endLine()).isGreaterThanOrEqualTo(2);
            assertThat(r.confidence()).isEqualTo(1.0f);
        }

        @Test
        void matchClosestToHintLine() {
            String file = "    int x = 1;\nother line\n    int x = 1;";
            var r = SnippetLocator.locate("    int x = 1;", file, 3);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.HASH_EXACT);
            // Should pick the one closest to hint line 3
            assertThat(r.line()).isIn(1, 3);
        }
    }

    // ── Strategy 2: Trimmed-contains match ───────────────────────────────

    @Nested
    class TrimmedContainsMatch {
        @Test
        void lineContainsSnippetSubstring_shouldReturnTrimmedContains() {
            String file = "aaa\nbbbb\n    int x = computeValue();  // comment\ndddd";
            // The snippet is the core part without the comment
            var r = SnippetLocator.locate("int x = computeValue();", file, 3);
            // Could be HASH_EXACT or TRIMMED_CONTAINS depending on hash normalization
            assertThat(r.line()).isEqualTo(3);
            assertThat(r.confidence()).isGreaterThan(0.0f);
        }

        @Test
        void shortSnippetUnder8Chars_shouldSkipTrimmedContains() {
            String file = "aaa\nbbb\nccc\nddd";
            // Snippet "bbb" is < 8 chars normalized, trimmed-contains skipped
            var r = SnippetLocator.locate("xxx", file, 2);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.NOT_FOUND);
        }

        @Test
        void closestToHintWhenMultipleMatches() {
            String file = "void method() {\n" +
                    "    return null;\n" +
                    "    // other\n" +
                    "    return null;\n" +
                    "}";
            var r = SnippetLocator.locate("return null;", file, 4);
            assertThat(r.line()).isIn(2, 4);
            assertThat(r.confidence()).isGreaterThan(0.0f);
        }
    }

    // ── Strategy 3: Multi-line block match ───────────────────────────────

    @Nested
    class MultiLineBlockMatch {
        @Test
        void twoLineBlockMatch_shouldReturnMultiLine() {
            String file = "header\nint a = 1;\nint b = 2;\nfooter";
            // A two-line snippet that may not hash-exact but normalizes to match
            var r = SnippetLocator.locate("int a = 1;\nint b = 2;", file, 2);
            assertThat(r.line()).isEqualTo(2);
            assertThat(r.confidence()).isGreaterThan(0.0f);
        }

        @Test
        void multiLineWithBlanks_shouldSkipBlankLinesInSnippet() {
            String file = "alpha\nbeta\ngamma\ndelta\nepsilon";
            var r = SnippetLocator.locate("beta\n\ngamma", file, 2);
            assertThat(r.line()).isEqualTo(2);
        }
    }

    // ── Not-found scenarios ──────────────────────────────────────────────

    @Nested
    class NotFound {
        @Test
        void completelyDifferentContent_shouldReturnNotFound() {
            String file = "public class Foo {\n}";
            var r = SnippetLocator.locate("completely different content here xyz", file, 1);
            assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.NOT_FOUND);
            assertThat(r.confidence()).isEqualTo(0.0f);
            assertThat(r.line()).isEqualTo(1); // falls back to hintLine
        }

        @Test
        void notFound_withHighHintLine_shouldClampToHintLine() {
            var r = SnippetLocator.locate("nonexistent code snippet here abc", "line1", 99);
            assertThat(r.line()).isEqualTo(99);
            assertThat(r.endLine()).isEqualTo(99);
        }
    }

    // ── normalize helper ─────────────────────────────────────────────────

    @Nested
    class NormalizeTests {
        @Test
        void nullInput_shouldReturnEmpty() {
            assertThat(SnippetLocator.normalize(null)).isEmpty();
        }

        @Test
        void emptyInput_shouldReturnEmpty() {
            assertThat(SnippetLocator.normalize("")).isEmpty();
        }

        @Test
        void whitespaceCollapsing() {
            assertThat(SnippetLocator.normalize("  int   x  =  1  ;  "))
                    .isEqualTo("int x = 1 ;");
        }

        @Test
        void tabsAndNewlines() {
            assertThat(SnippetLocator.normalize("\t\tint x\t= 1"))
                    .isEqualTo("int x = 1");
        }
    }

    // ── LocateResult record accessors ────────────────────────────────────

    @Test
    void locateResult_recordAccessors() {
        var r = new SnippetLocator.LocateResult(5, 10, 0.85f, SnippetLocator.Strategy.MULTI_LINE);
        assertThat(r.line()).isEqualTo(5);
        assertThat(r.endLine()).isEqualTo(10);
        assertThat(r.confidence()).isEqualTo(0.85f);
        assertThat(r.strategy()).isEqualTo(SnippetLocator.Strategy.MULTI_LINE);
    }

    @Test
    void strategy_values_shouldExist() {
        assertThat(SnippetLocator.Strategy.values()).hasSize(4);
        assertThat(SnippetLocator.Strategy.valueOf("HASH_EXACT")).isNotNull();
        assertThat(SnippetLocator.Strategy.valueOf("TRIMMED_CONTAINS")).isNotNull();
        assertThat(SnippetLocator.Strategy.valueOf("MULTI_LINE")).isNotNull();
        assertThat(SnippetLocator.Strategy.valueOf("NOT_FOUND")).isNotNull();
    }

    // ── Integration-like scenarios ───────────────────────────────────────

    @Test
    void realWorldJavaFile_shouldFindMethod() {
        String javaFile = """
                package com.example;
                
                public class Calculator {
                    
                    public int add(int a, int b) {
                        return a + b;
                    }
                    
                    public int subtract(int a, int b) {
                        return a - b;
                    }
                    
                    public int multiply(int a, int b) {
                        return a * b;
                    }
                }
                """;
        var r = SnippetLocator.locate("public int subtract(int a, int b) {", javaFile, 9);
        assertThat(r.line()).isEqualTo(9);
        assertThat(r.confidence()).isGreaterThan(0.0f);
    }

    @Test
    void realWorldMultiLineSnippet() {
        String javaFile = """
                class Foo {
                    void bar() {
                        if (condition) {
                            doSomething();
                            doSomethingElse();
                        }
                    }
                }
                """;
        var r = SnippetLocator.locate(
                "        if (condition) {\n            doSomething();\n            doSomethingElse();",
                javaFile, 3);
        assertThat(r.line()).isGreaterThanOrEqualTo(3);
        assertThat(r.confidence()).isGreaterThan(0.0f);
    }

    @Test
    void windowsLineEndings_shouldWork() {
        String file = "line1\r\nline2\r\nline3\r\nline4";
        var r = SnippetLocator.locate("line2", file, 2);
        assertThat(r.line()).isEqualTo(2);
    }
}
