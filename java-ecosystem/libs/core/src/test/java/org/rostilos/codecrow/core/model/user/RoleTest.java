package org.rostilos.codecrow.core.model.user;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class RoleTest {

    @Test
    void testDefaultConstructor() {
        Role role = new Role();
        assertThat(role.getId()).isNull();
        assertThat(role.getName()).isNull();
    }

    @Test
    void testConstructorWithName() {
        Role role = new Role(ERole.ROLE_USER);
        assertThat(role.getId()).isNull();
        assertThat(role.getName()).isEqualTo(ERole.ROLE_USER);
    }

    @Test
    void testSetAndGetId() {
        Role role = new Role();
        role.setId(1);
        assertThat(role.getId()).isEqualTo(1);
    }

    @Test
    void testSetAndGetName() {
        Role role = new Role();
        role.setName(ERole.ROLE_ADMIN);
        assertThat(role.getName()).isEqualTo(ERole.ROLE_ADMIN);
    }

    @Test
    void testFullRoleSetup() {
        Role role = new Role(ERole.ROLE_USER);
        role.setId(5);
        
        assertThat(role.getId()).isEqualTo(5);
        assertThat(role.getName()).isEqualTo(ERole.ROLE_USER);
    }

    @Test
    void testRoleWithAdminRole() {
        Role role = new Role(ERole.ROLE_ADMIN);
        assertThat(role.getName()).isEqualTo(ERole.ROLE_ADMIN);
    }

    @Test
    void testRoleWithModeratorRole() {
        Role role = new Role(ERole.ROLE_MODERATOR);
        assertThat(role.getName()).isEqualTo(ERole.ROLE_MODERATOR);
    }

    @Test
    void testSetIdMultipleTimes() {
        Role role = new Role();
        role.setId(1);
        role.setId(2);
        assertThat(role.getId()).isEqualTo(2);
    }

    @Test
    void testSetNameMultipleTimes() {
        Role role = new Role();
        role.setName(ERole.ROLE_USER);
        role.setName(ERole.ROLE_ADMIN);
        assertThat(role.getName()).isEqualTo(ERole.ROLE_ADMIN);
    }
}
