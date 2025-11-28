package org.rostilos.codecrow.core.persistence.repository;

import org.rostilos.codecrow.core.model.Configuration;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface ConfigurationRepository extends JpaRepository<Configuration, Long> {
    List<Configuration> findByUserId(Long userId);
    Configuration findByUserIdAndConfigKey(Long userId, String configKey);
}
