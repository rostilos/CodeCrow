package org.rostilos.codecrow.core.model.project;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("EProjectRole")
class EProjectRoleTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        EProjectRole[] values = EProjectRole.values();
        
        assertThat(values).hasSize(3);
        assertThat(values).contains(
                EProjectRole.OWNER,
                EProjectRole.MAINTAINER,
                EProjectRole.VIEWER
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(EProjectRole.valueOf("OWNER")).isEqualTo(EProjectRole.OWNER);
        assertThat(EProjectRole.valueOf("MAINTAINER")).isEqualTo(EProjectRole.MAINTAINER);
        assertThat(EProjectRole.valueOf("VIEWER")).isEqualTo(EProjectRole.VIEWER);
    }

    @Test
    @DisplayName("ordinal should reflect privilege order")
    void ordinalShouldReflectPrivilegeOrder() {
        assertThat(EProjectRole.OWNER.ordinal()).isLessThan(EProjectRole.MAINTAINER.ordinal());
        assertThat(EProjectRole.MAINTAINER.ordinal()).isLessThan(EProjectRole.VIEWER.ordinal());
    }
}
