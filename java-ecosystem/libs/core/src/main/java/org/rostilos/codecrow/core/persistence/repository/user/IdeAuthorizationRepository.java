package org.rostilos.codecrow.core.persistence.repository.user;

import org.rostilos.codecrow.core.model.user.IdeAuthorization;
import org.springframework.data.jpa.repository.*;
import org.springframework.data.repository.query.Param;
import java.time.Instant;
import java.util.Optional;

public interface IdeAuthorizationRepository extends JpaRepository<IdeAuthorization, Long> {
    Optional<IdeAuthorization> findByDeviceHash(String hash);
    @Lock(jakarta.persistence.LockModeType.PESSIMISTIC_WRITE)
    @Query("SELECT a FROM IdeAuthorization a WHERE a.userCode = :code")
    Optional<IdeAuthorization> findForApproval(@Param("code") String code);
    @Modifying
    @Query("DELETE FROM IdeAuthorization a WHERE a.expiresAt < :now")
    void deleteExpired(@Param("now") Instant now);
}
