package org.rostilos.codecrow.core.persistence.repository.user;

import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.core.model.user.twofactor.TwoFactorAuth;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface TwoFactorAuthRepository extends JpaRepository<TwoFactorAuth, Long> {
    Optional<TwoFactorAuth> findByUser(User user);
    Optional<TwoFactorAuth> findByUserId(Long userId);
    boolean existsByUserIdAndEnabledTrue(Long userId);
    void deleteByUser(User user);
}
