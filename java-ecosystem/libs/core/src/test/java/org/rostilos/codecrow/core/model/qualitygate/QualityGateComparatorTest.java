package org.rostilos.codecrow.core.model.qualitygate;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("QualityGateComparator")
class QualityGateComparatorTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        QualityGateComparator[] values = QualityGateComparator.values();
        
        assertThat(values).hasSize(6);
        assertThat(values).contains(
                QualityGateComparator.GREATER_THAN,
                QualityGateComparator.GREATER_THAN_OR_EQUAL,
                QualityGateComparator.LESS_THAN,
                QualityGateComparator.LESS_THAN_OR_EQUAL,
                QualityGateComparator.EQUAL,
                QualityGateComparator.NOT_EQUAL
        );
    }

    @Test
    @DisplayName("getSymbol should return correct symbol")
    void getSymbolShouldReturnCorrectSymbol() {
        assertThat(QualityGateComparator.GREATER_THAN.getSymbol()).isEqualTo(">");
        assertThat(QualityGateComparator.GREATER_THAN_OR_EQUAL.getSymbol()).isEqualTo(">=");
        assertThat(QualityGateComparator.LESS_THAN.getSymbol()).isEqualTo("<");
        assertThat(QualityGateComparator.LESS_THAN_OR_EQUAL.getSymbol()).isEqualTo("<=");
        assertThat(QualityGateComparator.EQUAL.getSymbol()).isEqualTo("==");
        assertThat(QualityGateComparator.NOT_EQUAL.getSymbol()).isEqualTo("!=");
    }

    @Test
    @DisplayName("getDescription should return correct description")
    void getDescriptionShouldReturnCorrectDescription() {
        assertThat(QualityGateComparator.GREATER_THAN.getDescription()).isEqualTo("greater than");
        assertThat(QualityGateComparator.GREATER_THAN_OR_EQUAL.getDescription()).isEqualTo("greater than or equal to");
        assertThat(QualityGateComparator.LESS_THAN.getDescription()).isEqualTo("less than");
        assertThat(QualityGateComparator.LESS_THAN_OR_EQUAL.getDescription()).isEqualTo("less than or equal to");
        assertThat(QualityGateComparator.EQUAL.getDescription()).isEqualTo("equal to");
        assertThat(QualityGateComparator.NOT_EQUAL.getDescription()).isEqualTo("not equal to");
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(QualityGateComparator.valueOf("GREATER_THAN")).isEqualTo(QualityGateComparator.GREATER_THAN);
        assertThat(QualityGateComparator.valueOf("LESS_THAN")).isEqualTo(QualityGateComparator.LESS_THAN);
        assertThat(QualityGateComparator.valueOf("EQUAL")).isEqualTo(QualityGateComparator.EQUAL);
    }
}
