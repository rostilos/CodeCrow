package org.rostilos.codecrow.core.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BranchPatternMatcher")
class BranchPatternMatcherTest {

    @Nested
    @DisplayName("matches() - single pattern matching")
    class MatchesTests {

        @Test
        @DisplayName("should match exact branch name")
        void shouldMatchExactBranchName() {
            assertThat(BranchPatternMatcher.matches("main", "main")).isTrue();
            assertThat(BranchPatternMatcher.matches("develop", "develop")).isTrue();
            assertThat(BranchPatternMatcher.matches("feature/auth", "feature/auth")).isTrue();
        }

        @Test
        @DisplayName("should not match different exact branch name")
        void shouldNotMatchDifferentBranchName() {
            assertThat(BranchPatternMatcher.matches("main", "develop")).isFalse();
            assertThat(BranchPatternMatcher.matches("feature/auth", "feature/login")).isFalse();
        }

        @Test
        @DisplayName("should match single wildcard pattern")
        void shouldMatchSingleWildcard() {
            // Single * should match any characters except /
            assertThat(BranchPatternMatcher.matches("release/1.0", "release/*")).isTrue();
            assertThat(BranchPatternMatcher.matches("release/v2.5.3", "release/*")).isTrue();
            assertThat(BranchPatternMatcher.matches("feature-123", "feature-*")).isTrue();
        }

        @Test
        @DisplayName("should not match nested paths with single wildcard")
        void shouldNotMatchNestedPathsWithSingleWildcard() {
            // Single * should NOT match paths with /
            assertThat(BranchPatternMatcher.matches("release/1.0/hotfix", "release/*")).isFalse();
            assertThat(BranchPatternMatcher.matches("feature/auth/oauth", "feature/*")).isFalse();
        }

        @Test
        @DisplayName("should match double wildcard pattern")
        void shouldMatchDoubleWildcard() {
            // Double ** should match any characters including /
            assertThat(BranchPatternMatcher.matches("feature/foo", "feature/**")).isTrue();
            assertThat(BranchPatternMatcher.matches("feature/foo/bar", "feature/**")).isTrue();
            assertThat(BranchPatternMatcher.matches("feature/deep/nested/path", "feature/**")).isTrue();
        }

        @Test
        @DisplayName("should match pattern with wildcards in middle")
        void shouldMatchPatternWithWildcardInMiddle() {
            assertThat(BranchPatternMatcher.matches("release/v1.0/stable", "release/*/stable")).isTrue();
            assertThat(BranchPatternMatcher.matches("release/v2.0/stable", "release/*/stable")).isTrue();
        }

        @Test
        @DisplayName("should match pattern starting with wildcard")
        void shouldMatchPatternStartingWithWildcard() {
            assertThat(BranchPatternMatcher.matches("feature-main", "*-main")).isTrue();
            assertThat(BranchPatternMatcher.matches("hotfix-main", "*-main")).isTrue();
        }

        @Test
        @DisplayName("should handle question mark wildcard with asterisk")
        void shouldHandleQuestionMarkWildcard() {
            // ? only works when pattern also contains * (implementation detail)
            // Pattern with both ? and * triggers glob-to-regex conversion
            assertThat(BranchPatternMatcher.matches("v1-release", "v?-*")).isTrue();
            assertThat(BranchPatternMatcher.matches("v2-release", "v?-*")).isTrue();
            assertThat(BranchPatternMatcher.matches("v12-release", "v?-*")).isFalse();
            // Test that ? doesn't match /
            assertThat(BranchPatternMatcher.matches("v/-release", "v?-*")).isFalse();
        }

        @Test
        @DisplayName("should treat question mark as literal when no asterisk in pattern")
        void shouldTreatQuestionMarkAsLiteralWithoutAsterisk() {
            // Without *, the pattern is treated as exact match
            assertThat(BranchPatternMatcher.matches("v?", "v?")).isTrue();
            assertThat(BranchPatternMatcher.matches("v1", "v?")).isFalse();
        }

        @ParameterizedTest
        @NullAndEmptySource
        @DisplayName("should return false for null or empty branch name")
        void shouldReturnFalseForNullOrEmptyBranchName(String branchName) {
            assertThat(BranchPatternMatcher.matches(branchName, "main")).isFalse();
        }

        @ParameterizedTest
        @NullAndEmptySource
        @DisplayName("should return false for null or empty pattern")
        void shouldReturnFalseForNullOrEmptyPattern(String pattern) {
            assertThat(BranchPatternMatcher.matches("main", pattern)).isFalse();
        }

        @Test
        @DisplayName("should escape regex special characters in pattern")
        void shouldEscapeRegexSpecialCharacters() {
            // Patterns with regex special chars should be escaped
            assertThat(BranchPatternMatcher.matches("feature.auth", "feature.auth")).isTrue();
            assertThat(BranchPatternMatcher.matches("feature+auth", "feature+auth")).isTrue();
            assertThat(BranchPatternMatcher.matches("feature(1)", "feature(1)")).isTrue();
        }
    }

    @Nested
    @DisplayName("matchesAny() - multiple pattern matching")
    class MatchesAnyTests {

        @Test
        @DisplayName("should match when branch matches first pattern")
        void shouldMatchFirstPattern() {
            List<String> patterns = Arrays.asList("main", "develop", "release/*");
            assertThat(BranchPatternMatcher.matchesAny("main", patterns)).isTrue();
        }

        @Test
        @DisplayName("should match when branch matches any pattern")
        void shouldMatchAnyPattern() {
            List<String> patterns = Arrays.asList("main", "develop", "release/*");
            assertThat(BranchPatternMatcher.matchesAny("release/1.0", patterns)).isTrue();
        }

        @Test
        @DisplayName("should not match when branch matches no pattern")
        void shouldNotMatchNoPattern() {
            List<String> patterns = Arrays.asList("main", "develop", "release/*");
            assertThat(BranchPatternMatcher.matchesAny("feature/auth", patterns)).isFalse();
        }

        @Test
        @DisplayName("should return false for null branch name")
        void shouldReturnFalseForNullBranchName() {
            List<String> patterns = Arrays.asList("main", "develop");
            assertThat(BranchPatternMatcher.matchesAny(null, patterns)).isFalse();
        }

        @Test
        @DisplayName("should return false for null patterns list")
        void shouldReturnFalseForNullPatterns() {
            assertThat(BranchPatternMatcher.matchesAny("main", null)).isFalse();
        }

        @Test
        @DisplayName("should return false for empty patterns list")
        void shouldReturnFalseForEmptyPatterns() {
            assertThat(BranchPatternMatcher.matchesAny("main", Collections.emptyList())).isFalse();
        }
    }

    @Nested
    @DisplayName("shouldAnalyze() - analysis decision")
    class ShouldAnalyzeTests {

        @Test
        @DisplayName("should allow all branches when patterns is null")
        void shouldAllowAllBranchesWhenPatternsNull() {
            assertThat(BranchPatternMatcher.shouldAnalyze("any-branch", null)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("feature/new", null)).isTrue();
        }

        @Test
        @DisplayName("should allow all branches when patterns is empty")
        void shouldAllowAllBranchesWhenPatternsEmpty() {
            assertThat(BranchPatternMatcher.shouldAnalyze("any-branch", Collections.emptyList())).isTrue();
        }

        @Test
        @DisplayName("should allow branch matching configured patterns")
        void shouldAllowBranchMatchingPatterns() {
            List<String> patterns = Arrays.asList("main", "develop", "release/**");
            
            assertThat(BranchPatternMatcher.shouldAnalyze("main", patterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("develop", patterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("release/1.0", patterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("release/1.0/hotfix", patterns)).isTrue();
        }

        @Test
        @DisplayName("should skip branch not matching configured patterns")
        void shouldSkipBranchNotMatchingPatterns() {
            List<String> patterns = Arrays.asList("main", "develop");
            
            assertThat(BranchPatternMatcher.shouldAnalyze("feature/auth", patterns)).isFalse();
            assertThat(BranchPatternMatcher.shouldAnalyze("hotfix/urgent", patterns)).isFalse();
        }
    }

    @Nested
    @DisplayName("Real-world pattern scenarios")
    class RealWorldScenarios {

        @ParameterizedTest
        @CsvSource({
            "main, main, true",
            "develop, develop, true",
            "feature/user-auth, feature/*, true",
            "feature/user-auth, feature/**, true",
            "feature/deep/nested, feature/**, true",
            "feature/deep/nested, feature/*, false",
            "release/v1.0.0, release/v*, true",
            "release/v1.0.0, release/*, true",
            "hotfix/SEC-123, hotfix/SEC-*, true",
            "bugfix/JIRA-456, bugfix/JIRA-*, true",
        })
        @DisplayName("should handle common Git branching patterns")
        void shouldHandleCommonGitBranchingPatterns(String branch, String pattern, boolean expected) {
            assertThat(BranchPatternMatcher.matches(branch, pattern)).isEqualTo(expected);
        }

        @Test
        @DisplayName("should handle GitFlow pattern configuration")
        void shouldHandleGitFlowPatterns() {
            List<String> gitFlowPatterns = Arrays.asList(
                "main",
                "develop", 
                "feature/**",
                "release/**",
                "hotfix/**"
            );

            // Should match
            assertThat(BranchPatternMatcher.shouldAnalyze("main", gitFlowPatterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("develop", gitFlowPatterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("feature/auth", gitFlowPatterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("feature/auth/oauth", gitFlowPatterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("release/1.0", gitFlowPatterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("hotfix/security", gitFlowPatterns)).isTrue();

            // Should not match
            assertThat(BranchPatternMatcher.shouldAnalyze("bugfix/typo", gitFlowPatterns)).isFalse();
            assertThat(BranchPatternMatcher.shouldAnalyze("experiment/ai", gitFlowPatterns)).isFalse();
        }

        @Test
        @DisplayName("should handle trunk-based development patterns")
        void shouldHandleTrunkBasedPatterns() {
            List<String> trunkPatterns = Arrays.asList("main", "trunk");

            assertThat(BranchPatternMatcher.shouldAnalyze("main", trunkPatterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("trunk", trunkPatterns)).isTrue();
            assertThat(BranchPatternMatcher.shouldAnalyze("feature/x", trunkPatterns)).isFalse();
        }
    }
}
