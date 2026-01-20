package org.rostilos.codecrow.core.dto.workspace;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("WorkspaceDTO")
class WorkspaceDTOTest {

    @Test
    @DisplayName("should create record with all fields")
    void shouldCreateRecordWithAllFields() {
        OffsetDateTime now = OffsetDateTime.now();
        
        WorkspaceDTO dto = new WorkspaceDTO(
                1L,
                "test-workspace",
                "Test Workspace",
                "A test workspace description",
                true,
                5L,
                now,
                now
        );

        assertThat(dto.id()).isEqualTo(1L);
        assertThat(dto.slug()).isEqualTo("test-workspace");
        assertThat(dto.name()).isEqualTo("Test Workspace");
        assertThat(dto.description()).isEqualTo("A test workspace description");
        assertThat(dto.active()).isTrue();
        assertThat(dto.membersCount()).isEqualTo(5L);
        assertThat(dto.updatedAt()).isEqualTo(now);
        assertThat(dto.createdAt()).isEqualTo(now);
    }

    @Test
    @DisplayName("should handle inactive workspace")
    void shouldHandleInactiveWorkspace() {
        WorkspaceDTO dto = new WorkspaceDTO(
                1L,
                "inactive",
                "Inactive Workspace",
                null,
                false,
                0L,
                null,
                null
        );

        assertThat(dto.active()).isFalse();
    }

    @Test
    @DisplayName("should handle null description")
    void shouldHandleNullDescription() {
        WorkspaceDTO dto = new WorkspaceDTO(1L, "slug", "name", null, true, 0L, null, null);

        assertThat(dto.description()).isNull();
    }

    @Test
    @DisplayName("should support equality based on fields")
    void shouldSupportEqualityBasedOnFields() {
        OffsetDateTime now = OffsetDateTime.now();
        WorkspaceDTO dto1 = new WorkspaceDTO(1L, "slug", "name", "desc", true, 5L, now, now);
        WorkspaceDTO dto2 = new WorkspaceDTO(1L, "slug", "name", "desc", true, 5L, now, now);

        assertThat(dto1).isEqualTo(dto2);
    }

    @Test
    @DisplayName("should support inequality when fields differ")
    void shouldSupportInequalityWhenFieldsDiffer() {
        OffsetDateTime now = OffsetDateTime.now();
        WorkspaceDTO dto1 = new WorkspaceDTO(1L, "slug1", "name", "desc", true, 5L, now, now);
        WorkspaceDTO dto2 = new WorkspaceDTO(1L, "slug2", "name", "desc", true, 5L, now, now);

        assertThat(dto1).isNotEqualTo(dto2);
    }
}
