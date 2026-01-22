package org.rostilos.codecrow.core.dto.user;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.user.User;

import java.lang.reflect.Field;
import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("UserDTO")
class UserDTOTest {

    @Nested
    @DisplayName("record constructor")
    class RecordConstructorTests {

        @Test
        @DisplayName("should create UserDTO with all fields")
        void shouldCreateWithAllFields() {
            Instant now = Instant.now();
            UserDTO dto = new UserDTO(1L, "testuser", "test@example.com", "TestCorp", "https://avatar.example.com/user.png", now);

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.username()).isEqualTo("testuser");
            assertThat(dto.email()).isEqualTo("test@example.com");
            assertThat(dto.company()).isEqualTo("TestCorp");
            assertThat(dto.avatarUrl()).isEqualTo("https://avatar.example.com/user.png");
            assertThat(dto.createdAt()).isEqualTo(now);
        }

        @Test
        @DisplayName("should create UserDTO with null optional fields")
        void shouldCreateWithNullOptionalFields() {
            UserDTO dto = new UserDTO(1L, "user", "user@test.com", null, null, null);

            assertThat(dto.company()).isNull();
            assertThat(dto.avatarUrl()).isNull();
            assertThat(dto.createdAt()).isNull();
        }
    }

    @Nested
    @DisplayName("fromUser()")
    class FromUserTests {

        @Test
        @DisplayName("should convert User with all fields")
        void shouldConvertWithAllFields() {
            User user = new User();
            user.setId(1L);
            user.setUsername("testuser");
            user.setEmail("test@example.com");
            user.setCompany("TestCorp");
            user.setAvatarUrl("https://avatar.example.com/pic.png");
            setField(user, "createdAt", Instant.now());

            UserDTO dto = UserDTO.fromUser(user);

            assertThat(dto.id()).isEqualTo(1L);
            assertThat(dto.username()).isEqualTo("testuser");
            assertThat(dto.email()).isEqualTo("test@example.com");
            assertThat(dto.company()).isEqualTo("TestCorp");
            assertThat(dto.avatarUrl()).isEqualTo("https://avatar.example.com/pic.png");
            assertThat(dto.createdAt()).isNotNull();
        }

        @Test
        @DisplayName("should convert User with null optional fields")
        void shouldConvertWithNullOptionalFields() {
            User user = new User();
            user.setId(2L);
            user.setUsername("minuser");
            user.setEmail("min@test.com");
            user.setCompany(null);
            user.setAvatarUrl(null);

            UserDTO dto = UserDTO.fromUser(user);

            assertThat(dto.company()).isNull();
            assertThat(dto.avatarUrl()).isNull();
        }

        @Test
        @DisplayName("should handle createdAt timestamp")
        void shouldHandleCreatedAtTimestamp() {
            Instant expectedTime = Instant.parse("2024-01-15T10:30:00Z");
            User user = new User();
            user.setId(3L);
            user.setUsername("timed");
            user.setEmail("timed@test.com");
            setField(user, "createdAt", expectedTime);

            UserDTO dto = UserDTO.fromUser(user);

            assertThat(dto.createdAt()).isEqualTo(expectedTime);
        }
    }

    @Nested
    @DisplayName("equality and hashCode")
    class EqualityTests {

        @Test
        @DisplayName("should be equal for same values")
        void shouldBeEqualForSameValues() {
            Instant now = Instant.now();
            UserDTO dto1 = new UserDTO(1L, "user", "user@test.com", "Corp", "avatar.png", now);
            UserDTO dto2 = new UserDTO(1L, "user", "user@test.com", "Corp", "avatar.png", now);

            assertThat(dto1).isEqualTo(dto2);
            assertThat(dto1.hashCode()).isEqualTo(dto2.hashCode());
        }

        @Test
        @DisplayName("should not be equal for different values")
        void shouldNotBeEqualForDifferentValues() {
            Instant now = Instant.now();
            UserDTO dto1 = new UserDTO(1L, "user1", "user1@test.com", "Corp", "avatar.png", now);
            UserDTO dto2 = new UserDTO(2L, "user2", "user2@test.com", "Corp", "avatar.png", now);

            assertThat(dto1).isNotEqualTo(dto2);
        }
    }

    private void setField(Object obj, String fieldName, Object value) {
        try {
            Field field = obj.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(obj, value);
        } catch (Exception e) {
            throw new RuntimeException("Failed to set field " + fieldName, e);
        }
    }
}
