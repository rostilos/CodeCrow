package org.rostilos.codecrow.core.dto.user;

import org.rostilos.codecrow.core.model.user.User;

import java.time.Instant;

public record UserDTO(Long id, String username, String email, String company, String avatarUrl, Instant createdAt) {
    public static UserDTO fromUser(User user) {
        return new UserDTO(
                user.getId(),
                user.getUsername(),
                user.getEmail(),
                user.getCompany(),
                user.getAvatarUrl(),
                user.getCreatedAt()
        );
    }
}
