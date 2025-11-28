package org.rostilos.codecrow.webserver.service;

import org.rostilos.codecrow.core.model.Configuration;
import org.rostilos.codecrow.core.persistence.repository.ConfigurationRepository;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class ConfigurationService {

    private final ConfigurationRepository configurationRepository;

    public ConfigurationService(ConfigurationRepository configurationRepository) {
        this.configurationRepository = configurationRepository;
    }

    public Configuration saveConfiguration(Configuration config) {
        return configurationRepository.save(config);
    }

    public Configuration getConfiguration(Long userId, String configKey) {
        return configurationRepository.findByUserIdAndConfigKey(userId, configKey);
    }

    public List<Configuration> getAllConfigurationsForUser(Long userId) {
        return configurationRepository.findByUserId(userId);
    }

    public void deleteConfiguration(Long id) {
        configurationRepository.deleteById(id);
    }
}
