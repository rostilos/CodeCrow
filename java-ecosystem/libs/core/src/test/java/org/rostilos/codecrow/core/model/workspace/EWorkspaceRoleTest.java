package org.rostilos.codecrow.core.model.workspace;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("EWorkspaceRole")
class EWorkspaceRoleTest {

    @Test
    @DisplayName("should have all expected values")
    void shouldHaveAllExpectedValues() {
        EWorkspaceRole[] values = EWorkspaceRole.values();
        
        assertThat(values).hasSize(4);
        assertThat(values).contains(
                EWorkspaceRole.OWNER,
                EWorkspaceRole.ADMIN,
                EWorkspaceRole.MEMBER,
                EWorkspaceRole.REVIEWER
        );
    }

    @Test
    @DisplayName("valueOf should return correct enum")
    void valueOfShouldReturnCorrectEnum() {
        assertThat(EWorkspaceRole.valueOf("OWNER")).isEqualTo(EWorkspaceRole.OWNER);
        assertThat(EWorkspaceRole.valueOf("ADMIN")).isEqualTo(EWorkspaceRole.ADMIN);
        assertThat(EWorkspaceRole.valueOf("MEMBER")).isEqualTo(EWorkspaceRole.MEMBER);
        assertThat(EWorkspaceRole.valueOf("REVIEWER")).isEqualTo(EWorkspaceRole.REVIEWER);
    }

    @Test
    @DisplayName("ordinal should reflect privilege order")
    void ordinalShouldReflectPrivilegeOrder() {
        assertThat(EWorkspaceRole.OWNER.ordinal()).isLessThan(EWorkspaceRole.ADMIN.ordinal());
        assertThat(EWorkspaceRole.ADMIN.ordinal()).isLessThan(EWorkspaceRole.MEMBER.ordinal());
        assertThat(EWorkspaceRole.MEMBER.ordinal()).isLessThan(EWorkspaceRole.REVIEWER.ordinal());
    }
}
