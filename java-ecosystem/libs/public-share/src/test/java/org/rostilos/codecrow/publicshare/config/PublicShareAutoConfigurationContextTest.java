package org.rostilos.codecrow.publicshare.config;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.publicshare.persistence.PublicShareLinkRepository;
import org.rostilos.codecrow.publicshare.service.PublicShareLinkService;
import org.springframework.boot.autoconfigure.AutoConfigurations;
import org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration;
import org.springframework.boot.autoconfigure.orm.jpa.HibernateJpaAutoConfiguration;
import org.springframework.boot.autoconfigure.transaction.TransactionAutoConfiguration;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

import static org.assertj.core.api.Assertions.assertThat;

class PublicShareAutoConfigurationContextTest {

    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withConfiguration(AutoConfigurations.of(
                    DataSourceAutoConfiguration.class,
                    HibernateJpaAutoConfiguration.class,
                    TransactionAutoConfiguration.class,
                    PublicShareAutoConfiguration.class
            ))
            .withPropertyValues(
                    "spring.datasource.url=jdbc:h2:mem:public-share;MODE=PostgreSQL;DB_CLOSE_DELAY=-1",
                    "spring.datasource.username=sa",
                    "spring.datasource.password=",
                    "spring.jpa.hibernate.ddl-auto=create-drop"
            );

    @Test
    void registersAndPersistsWithoutApplicationPackageScanning() {
        contextRunner.run(context -> {
            assertThat(context).hasNotFailed();
            assertThat(context).hasSingleBean(PublicShareLinkRepository.class);
            assertThat(context).hasSingleBean(PublicShareLinkService.class);

            PublicShareLinkService service = context.getBean(PublicShareLinkService.class);
            String token = service.issue("test-resource", "internal-42").token();

            assertThat(service.resolve(token))
                    .hasValueSatisfying(resolved -> {
                        assertThat(resolved.resourceType()).isEqualTo("test-resource");
                        assertThat(resolved.resourceKey()).isEqualTo("internal-42");
                    });
        });
    }
}
