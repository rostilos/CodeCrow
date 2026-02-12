package org.rostilos.codecrow.core.persistence.repository.admin;

import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;
import org.rostilos.codecrow.core.model.admin.SiteSettings;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface SiteSettingsRepository extends JpaRepository<SiteSettings, Long> {

    List<SiteSettings> findByConfigGroup(ESiteSettingsGroup configGroup);

    Optional<SiteSettings> findByConfigGroupAndConfigKey(ESiteSettingsGroup configGroup, String configKey);

    void deleteByConfigGroup(ESiteSettingsGroup configGroup);

    boolean existsByConfigGroup(ESiteSettingsGroup configGroup);
}
