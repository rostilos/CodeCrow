package org.rostilos.codecrow.publicshare.persistence;

import org.rostilos.codecrow.publicshare.model.PublicShareLink;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface PublicShareLinkRepository extends JpaRepository<PublicShareLink, Long> {

    Optional<PublicShareLink> findByTokenHash(String tokenHash);
}
