package org.rostilos.codecrow.core.dto.workspace;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.workspace.EWorkspaceRole;
import org.rostilos.codecrow.core.model.workspace.EMembershipStatus;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.model.workspace.WorkspaceMember;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class WorkspaceMemberDTOTest {

    @Test
    void shouldCreateRecordWithAllFields() {
        Instant joinedAt = Instant.now();
        
        WorkspaceMemberDTO dto = new WorkspaceMemberDTO(
                1L,
                100L,
                "ADMIN",
                "ACTIVE",
                "testuser",
                "test@example.com",
                "https://avatar.url",
                joinedAt
        );
        
        assertThat(dto.id()).isEqualTo(1L);
        assertThat(dto.userId()).isEqualTo(100L);
        assertThat(dto.role()).isEqualTo("ADMIN");
        assertThat(dto.status()).isEqualTo("ACTIVE");
        assertThat(dto.username()).isEqualTo("testuser");
        assertThat(dto.email()).isEqualTo("test@example.com");
        assertThat(dto.avatarUrl()).isEqualTo("https://avatar.url");
        assertThat(dto.joinedAt()).isEqualTo(joinedAt);
    }

    @Test
    void fromEntity_shouldMapAllFields() {
        WorkspaceMember member = createTestWorkspaceMember();
        
        WorkspaceMemberDTO dto = WorkspaceMemberDTO.fromEntity(member);
        
        assertThat(dto.id()).isNull();
        assertThat(dto.userId()).isNull();
        assertThat(dto.role()).isEqualTo("MEMBER");
        assertThat(dto.status()).isEqualTo("ACTIVE");
        assertThat(dto.username()).isEqualTo("john_doe");
        assertThat(dto.email()).isEqualTo("john@example.com");
        assertThat(dto.avatarUrl()).isEqualTo("https://example.com/avatar.png");
        assertThat(dto.joinedAt()).isNull();
    }

    @Test
    void fromEntity_shouldHandleDifferentRoles() {
        WorkspaceMember member = createTestWorkspaceMember();
        member.setRole(EWorkspaceRole.ADMIN);
        
        WorkspaceMemberDTO dto = WorkspaceMemberDTO.fromEntity(member);
        
        assertThat(dto.role()).isEqualTo("ADMIN");
    }

    @Test
    void fromEntity_shouldHandleDifferentStatuses() {
        WorkspaceMember member = createTestWorkspaceMember();
        member.setStatus(EMembershipStatus.PENDING);
        
        WorkspaceMemberDTO dto = WorkspaceMemberDTO.fromEntity(member);
        
        assertThat(dto.status()).isEqualTo("PENDING");
    }

    @Test
    void shouldSupportEquality() {
        Instant now = Instant.now();
        WorkspaceMemberDTO dto1 = new WorkspaceMemberDTO(1L, 100L, "ADMIN", "ACTIVE", 
                "user", "email", "avatar", now);
        WorkspaceMemberDTO dto2 = new WorkspaceMemberDTO(1L, 100L, "ADMIN", "ACTIVE", 
                "user", "email", "avatar", now);
        
        assertThat(dto1).isEqualTo(dto2);
        assertThat(dto1.hashCode()).isEqualTo(dto2.hashCode());
    }

    @Test
    void shouldSupportInequality() {
        Instant now = Instant.now();
        WorkspaceMemberDTO dto1 = new WorkspaceMemberDTO(1L, 100L, "ADMIN", "ACTIVE", 
                "user", "email", "avatar", now);
        WorkspaceMemberDTO dto2 = new WorkspaceMemberDTO(2L, 100L, "ADMIN", "ACTIVE", 
                "user", "email", "avatar", now);
        
        assertThat(dto1).isNotEqualTo(dto2);
    }

    private WorkspaceMember createTestWorkspaceMember() {
        Workspace workspace = new Workspace();
        workspace.setName("Test Workspace");
        workspace.setSlug("test-workspace");
        
        User user = new User();
        user.setUsername("john_doe");
        user.setEmail("john@example.com");
        user.setAvatarUrl("https://example.com/avatar.png");
        
        WorkspaceMember member = new WorkspaceMember();
        member.setWorkspace(workspace);
        member.setUser(user);
        member.setRole(EWorkspaceRole.MEMBER);
        member.setStatus(EMembershipStatus.ACTIVE);
        
        return member;
    }
}
