package org.rostilos.codecrow.core.model.codeanalysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("IssueSeverity")
class IssueSeverityTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        IssueSeverity[] values = IssueSeverity.values();
        
        assertThat(values).hasSize(5);
        assertThat(values).contains(
                IssueSeverity.HIGH,
                IssueSeverity.MEDIUM,
                IssueSeverity.LOW,
                IssueSeverity.INFO,
                IssueSeverity.RESOLVED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(IssueSeverity.valueOf("HIGH")).isEqualTo(IssueSeverity.HIGH);
        assertThat(IssueSeverity.valueOf("LOW")).isEqualTo(IssueSeverity.LOW);
        assertThat(IssueSeverity.valueOf("RESOLVED")).isEqualTo(IssueSeverity.RESOLVED);
    }

    @Test
    @DisplayName("name should return string representation")
    void nameShouldReturnStringRepresentation() {
        assertThat(IssueSeverity.HIGH.name()).isEqualTo("HIGH");
        assertThat(IssueSeverity.INFO.name()).isEqualTo("INFO");
    }

    @Test
    @DisplayName("ordinal should reflect severity order")
    void ordinalShouldReflectSeverityOrder() {
        assertThat(IssueSeverity.HIGH.ordinal()).isLessThan(IssueSeverity.MEDIUM.ordinal());
        assertThat(IssueSeverity.MEDIUM.ordinal()).isLessThan(IssueSeverity.LOW.ordinal());
        assertThat(IssueSeverity.LOW.ordinal()).isLessThan(IssueSeverity.INFO.ordinal());
    }
}
