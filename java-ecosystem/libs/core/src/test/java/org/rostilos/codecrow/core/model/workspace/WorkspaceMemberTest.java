package org.rostilos.codecrow.core.model.workspace;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.User;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("WorkspaceMember Entity")
class WorkspaceMemberTest {

    private WorkspaceMember member;
    private Workspace workspace;
    private User user;

    @BeforeEach
    void setUp() {
        member = new WorkspaceMember();
        workspace = new Workspace("test-slug", "Test Workspace", "Description");
        user = new User("testuser", "test@example.com", "password", "Company");
    }

    @Nested
    @DisplayName("Constructors")
    class Constructors {

        @Test
        @DisplayName("should create member with default constructor")
        void shouldCreateMemberWithDefaultConstructor() {
            WorkspaceMember wm = new WorkspaceMember();
            
            assertThat(wm.getId()).isNull();
            assertThat(wm.getWorkspace()).isNull();
            assertThat(wm.getUser()).isNull();
            assertThat(wm.getRole()).isEqualTo(EWorkspaceRole.MEMBER);
            assertThat(wm.getStatus()).isEqualTo(EMembershipStatus.ACTIVE);
            assertThat(wm.getJoinedAt()).isNull();
        }

        @Test
        @DisplayName("should create member with parameterized constructor")
        void shouldCreateMemberWithParameterizedConstructor() {
            WorkspaceMember wm = new WorkspaceMember(workspace, user, EWorkspaceRole.ADMIN, EMembershipStatus.ACTIVE);
            
            assertThat(wm.getWorkspace()).isEqualTo(workspace);
            assertThat(wm.getUser()).isEqualTo(user);
            assertThat(wm.getRole()).isEqualTo(EWorkspaceRole.ADMIN);
            assertThat(wm.getStatus()).isEqualTo(EMembershipStatus.ACTIVE);
        }
    }

    @Nested
    @DisplayName("Workspace Association")
    class WorkspaceAssociation {

        @Test
        @DisplayName("should set and get workspace")
        void shouldSetAndGetWorkspace() {
            member.setWorkspace(workspace);
            
            assertThat(member.getWorkspace()).isEqualTo(workspace);
        }
    }

    @Nested
    @DisplayName("User Association")
    class UserAssociation {

        @Test
        @DisplayName("should set and get user")
        void shouldSetAndGetUser() {
            member.setUser(user);
            
            assertThat(member.getUser()).isEqualTo(user);
        }
    }

    @Nested
    @DisplayName("Role Management")
    class RoleManagement {

        @Test
        @DisplayName("should have MEMBER role by default")
        void shouldHaveMemberRoleByDefault() {
            assertThat(member.getRole()).isEqualTo(EWorkspaceRole.MEMBER);
        }

        @Test
        @DisplayName("should set and get role")
        void shouldSetAndGetRole() {
            member.setRole(EWorkspaceRole.ADMIN);
            
            assertThat(member.getRole()).isEqualTo(EWorkspaceRole.ADMIN);
        }

        @Test
        @DisplayName("should support OWNER role")
        void shouldSupportOwnerRole() {
            member.setRole(EWorkspaceRole.OWNER);
            
            assertThat(member.getRole()).isEqualTo(EWorkspaceRole.OWNER);
        }
    }

    @Nested
    @DisplayName("Status Management")
    class StatusManagement {

        @Test
        @DisplayName("should have ACTIVE status by default")
        void shouldHaveActiveStatusByDefault() {
            assertThat(member.getStatus()).isEqualTo(EMembershipStatus.ACTIVE);
        }

        @Test
        @DisplayName("should set and get status")
        void shouldSetAndGetStatus() {
            member.setStatus(EMembershipStatus.REVOKED);
            
            assertThat(member.getStatus()).isEqualTo(EMembershipStatus.REVOKED);
        }

        @Test
        @DisplayName("should support PENDING status")
        void shouldSupportPendingStatus() {
            member.setStatus(EMembershipStatus.PENDING);
            
            assertThat(member.getStatus()).isEqualTo(EMembershipStatus.PENDING);
        }
    }

    @Nested
    @DisplayName("Audit Fields")
    class AuditFields {

        @Test
        @DisplayName("should get joinedAt")
        void shouldGetJoinedAt() {
            // joinedAt is set by JPA auditing, so it will be null without persistence context
            assertThat(member.getJoinedAt()).isNull();
        }
    }
}
