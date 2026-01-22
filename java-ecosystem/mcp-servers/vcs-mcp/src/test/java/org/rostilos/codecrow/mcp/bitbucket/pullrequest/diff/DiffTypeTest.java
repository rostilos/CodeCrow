package org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("DiffType (package level enum)")
class DiffTypeTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        DiffType[] values = DiffType.values();
        
        assertThat(values).hasSize(4);
        assertThat(values).contains(
                DiffType.ADDED,
                DiffType.MODIFIED,
                DiffType.REMOVED,
                DiffType.RENAMED
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(DiffType.valueOf("ADDED")).isEqualTo(DiffType.ADDED);
        assertThat(DiffType.valueOf("MODIFIED")).isEqualTo(DiffType.MODIFIED);
        assertThat(DiffType.valueOf("REMOVED")).isEqualTo(DiffType.REMOVED);
        assertThat(DiffType.valueOf("RENAMED")).isEqualTo(DiffType.RENAMED);
    }
}
