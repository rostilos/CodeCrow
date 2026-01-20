package org.rostilos.codecrow.core.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("Configuration")
class ConfigurationTest {

    @Test
    @DisplayName("should set and get all fields")
    void shouldSetAndGetAllFields() {
        Configuration config = new Configuration();
        
        config.setId(1L);
        config.setConfigKey("api.key");
        config.setConfigValue("secret-value");
        config.setUserId(100L);
        
        assertThat(config.getId()).isEqualTo(1L);
        assertThat(config.getConfigKey()).isEqualTo("api.key");
        assertThat(config.getConfigValue()).isEqualTo("secret-value");
        assertThat(config.getUserId()).isEqualTo(100L);
    }

    @Test
    @DisplayName("should have null values initially")
    void shouldHaveNullValuesInitially() {
        Configuration config = new Configuration();
        
        assertThat(config.getId()).isNull();
        assertThat(config.getConfigKey()).isNull();
        assertThat(config.getConfigValue()).isNull();
        assertThat(config.getUserId()).isNull();
    }

    @Test
    @DisplayName("setId should update id")
    void setIdShouldUpdateId() {
        Configuration config = new Configuration();
        
        config.setId(42L);
        
        assertThat(config.getId()).isEqualTo(42L);
    }

    @Test
    @DisplayName("setConfigKey should update config key")
    void setConfigKeyShouldUpdateConfigKey() {
        Configuration config = new Configuration();
        
        config.setConfigKey("my.setting");
        
        assertThat(config.getConfigKey()).isEqualTo("my.setting");
    }

    @Test
    @DisplayName("setConfigValue should update config value")
    void setConfigValueShouldUpdateConfigValue() {
        Configuration config = new Configuration();
        
        config.setConfigValue("my-value");
        
        assertThat(config.getConfigValue()).isEqualTo("my-value");
    }

    @Test
    @DisplayName("setUserId should update user id")
    void setUserIdShouldUpdateUserId() {
        Configuration config = new Configuration();
        
        config.setUserId(999L);
        
        assertThat(config.getUserId()).isEqualTo(999L);
    }
}
