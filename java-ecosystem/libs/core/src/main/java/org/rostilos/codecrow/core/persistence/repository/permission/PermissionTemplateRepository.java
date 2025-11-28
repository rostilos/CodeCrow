package org.rostilos.codecrow.core.persistence.repository.permission;

import java.util.Optional;

import org.rostilos.codecrow.core.model.permission.PermissionTemplate;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PermissionTemplateRepository extends JpaRepository<PermissionTemplate, Long> {
    Optional<PermissionTemplate> findByName(String name);
}
