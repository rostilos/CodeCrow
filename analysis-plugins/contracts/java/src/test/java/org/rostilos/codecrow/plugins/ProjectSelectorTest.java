package org.rostilos.codecrow.plugins;

import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class ProjectSelectorTest {
    private static final Path FIXTURE = Path.of(
            System.getProperty("codecrow.plugin.fixtures"), "plugins-valid.json");

    @Test
    void selection_matches_the_shared_cross_runtime_projection() throws Exception {
        PluginRegistry registry = new PluginRegistry(
                new PluginManifestLoader().loadDescriptors(FIXTURE));
        RepositoryFacts facts = new RepositoryFacts(
                "abc1234",
                List.of(
                        "app/code/Vendor/Module/Model/Foo.php",
                        "app/etc/config.php",
                        "bin/magento",
                        "composer.json"),
                Map.of("composer.json", "{\"require\":{\"magento/framework\":\"*\"}}"));

        ProjectCapabilities selected = new ProjectSelector(registry).select(facts);

        assertThat(selected.repositoryPlugins()).containsExactly("php", "magento");
        assertThat(selected.filePlugins()).containsEntry(
                "app/code/Vendor/Module/Model/Foo.php", List.of("php"));
        assertThat(selected.detectionEvidence().get("magento")).containsExactly(
                "file:app/etc/config.php", "file:bin/magento", "file:composer.json");
        assertThat(selected.fingerprint()).isEqualTo(
                "sha256:6a888ce52e94cba767c754ff096d29c13637244976edcb97d9a68f44eeb43b10");
    }
}
