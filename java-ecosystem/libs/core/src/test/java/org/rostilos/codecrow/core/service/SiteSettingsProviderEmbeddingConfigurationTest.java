package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.admin.ESiteSettingsGroup;
import org.rostilos.codecrow.core.persistence.repository.admin.SiteSettingsRepository;
import org.rostilos.codecrow.core.security.TokenEncryptionService;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class SiteSettingsProviderEmbeddingConfigurationTest {

    @Mock
    private SiteSettingsRepository repository;

    @Mock
    private TokenEncryptionService encryptionService;

    private SiteSettingsProvider provider;

    @BeforeEach
    void setUp() {
        provider = new SiteSettingsProvider(repository, encryptionService);
    }

    @Test
    void defaultsAreNotAnExplicitEmbeddingConfiguration() {
        when(repository.existsByConfigGroup(ESiteSettingsGroup.EMBEDDING)).thenReturn(false);

        assertThat(provider.isEmbeddingConfigurationExplicitlySet()).isFalse();
    }

    @Test
    void siteAdminEmbeddingGroupIsExplicit() {
        when(repository.existsByConfigGroup(ESiteSettingsGroup.EMBEDDING)).thenReturn(true);

        assertThat(provider.isEmbeddingConfigurationExplicitlySet()).isTrue();
    }

    @Test
    void deploymentEmbeddingPropertyIsExplicit() {
        ReflectionTestUtils.setField(provider, "propEmbeddingProvider", "openrouter");

        assertThat(provider.isEmbeddingConfigurationExplicitlySet()).isTrue();
    }
}
