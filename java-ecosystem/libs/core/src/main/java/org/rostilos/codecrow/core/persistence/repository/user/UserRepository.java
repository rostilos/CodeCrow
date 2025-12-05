package org.rostilos.codecrow.core.persistence.repository.user;

import org.rostilos.codecrow.core.model.user.User;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface UserRepository extends JpaRepository<User, Long> {
    Optional<User> findByUsername(String username);
    Optional<User> findByEmail(String email);
    Optional<User> findByGoogleId(String googleId);
    Boolean existsByUsername(String username);
    Boolean existsByEmail(String email);
    Boolean existsByGoogleId(String googleId);
}
