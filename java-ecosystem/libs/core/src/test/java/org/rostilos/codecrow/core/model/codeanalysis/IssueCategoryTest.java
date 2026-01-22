package org.rostilos.codecrow.core.model.codeanalysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("IssueCategory")
class IssueCategoryTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        IssueCategory[] values = IssueCategory.values();
        
        assertThat(values).hasSize(10);
        assertThat(values).contains(
                IssueCategory.SECURITY,
                IssueCategory.PERFORMANCE,
                IssueCategory.CODE_QUALITY,
                IssueCategory.BUG_RISK,
                IssueCategory.STYLE,
                IssueCategory.DOCUMENTATION,
                IssueCategory.BEST_PRACTICES,
                IssueCategory.ERROR_HANDLING,
                IssueCategory.TESTING,
                IssueCategory.ARCHITECTURE
        );
    }

    @Test
    @DisplayName("should have display name for each category")
    void shouldHaveDisplayNameForEachCategory() {
        assertThat(IssueCategory.SECURITY.getDisplayName()).isEqualTo("Security");
        assertThat(IssueCategory.PERFORMANCE.getDisplayName()).isEqualTo("Performance");
        assertThat(IssueCategory.CODE_QUALITY.getDisplayName()).isEqualTo("Code Quality");
        assertThat(IssueCategory.BUG_RISK.getDisplayName()).isEqualTo("Bug Risk");
        assertThat(IssueCategory.ARCHITECTURE.getDisplayName()).isEqualTo("Architecture");
    }

    @Test
    @DisplayName("should have description for each category")
    void shouldHaveDescriptionForEachCategory() {
        assertThat(IssueCategory.SECURITY.getDescription()).contains("Security vulnerabilities");
        assertThat(IssueCategory.PERFORMANCE.getDescription()).contains("Performance bottlenecks");
        assertThat(IssueCategory.TESTING.getDescription()).contains("Test coverage");
    }

    @Nested
    @DisplayName("fromString")
    class FromString {

        @ParameterizedTest
        @NullAndEmptySource
        @ValueSource(strings = {"   ", "\t"})
        @DisplayName("should return CODE_QUALITY for null or blank input")
        void shouldReturnCodeQualityForNullOrBlank(String value) {
            IssueCategory result = IssueCategory.fromString(value);
            assertThat(result).isEqualTo(IssueCategory.CODE_QUALITY);
        }

        @Test
        @DisplayName("should parse exact enum names")
        void shouldParseExactEnumNames() {
            assertThat(IssueCategory.fromString("SECURITY")).isEqualTo(IssueCategory.SECURITY);
            assertThat(IssueCategory.fromString("PERFORMANCE")).isEqualTo(IssueCategory.PERFORMANCE);
            assertThat(IssueCategory.fromString("BUG_RISK")).isEqualTo(IssueCategory.BUG_RISK);
        }

        @Test
        @DisplayName("should parse display names case-insensitively")
        void shouldParseDisplayNamesCaseInsensitively() {
            assertThat(IssueCategory.fromString("Security")).isEqualTo(IssueCategory.SECURITY);
            assertThat(IssueCategory.fromString("code quality")).isEqualTo(IssueCategory.CODE_QUALITY);
            assertThat(IssueCategory.fromString("Bug Risk")).isEqualTo(IssueCategory.BUG_RISK);
        }

        @ParameterizedTest
        @CsvSource({
                "QUALITY, CODE_QUALITY",
                "CODE_SMELL, CODE_QUALITY",
                "MAINTAINABILITY, CODE_QUALITY",
                "BUG, BUG_RISK",
                "BUGS, BUG_RISK",
                "ERROR, BUG_RISK",
                "DEFECT, BUG_RISK",
                "PERF, PERFORMANCE",
                "SEC, SECURITY",
                "VULNERABILITY, SECURITY",
                "FORMAT, STYLE",
                "NAMING, STYLE",
                "DOCS, DOCUMENTATION",
                "EXCEPTION, ERROR_HANDLING",
                "TEST, TESTING",
                "DESIGN, ARCHITECTURE",
                "SOLID, ARCHITECTURE"
        })
        @DisplayName("should map aliases to correct category")
        void shouldMapAliasesToCorrectCategory(String alias, String expected) {
            assertThat(IssueCategory.fromString(alias)).isEqualTo(IssueCategory.valueOf(expected));
        }

        @Test
        @DisplayName("should return CODE_QUALITY for unknown values")
        void shouldReturnCodeQualityForUnknownValues() {
            assertThat(IssueCategory.fromString("unknown")).isEqualTo(IssueCategory.CODE_QUALITY);
            assertThat(IssueCategory.fromString("random")).isEqualTo(IssueCategory.CODE_QUALITY);
        }
    }

    @Test
    @DisplayName("getAllCategoriesForPrompt should return formatted string")
    void getAllCategoriesForPromptShouldReturnFormattedString() {
        String result = IssueCategory.getAllCategoriesForPrompt();
        
        assertThat(result).contains("- SECURITY:");
        assertThat(result).contains("- PERFORMANCE:");
        assertThat(result).contains("- CODE_QUALITY:");
        assertThat(result).contains("- BUG_RISK:");
        assertThat(result).contains("\n");
    }
}
