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
                "file:app/etc/config.php", "file:bin/magento", "file:composer.json", "root:.");
        assertThat(selected.fingerprint()).isEqualTo(
                "sha256:82da50c6916ad2b50e268523e6226aeaee6f9bb8e76fd868aed5419503946eaf");
    }

    @Test
    void detects_one_coherent_arbitrarily_nested_magento_root() throws Exception {
        PluginRegistry registry = new PluginRegistry(
                new PluginManifestLoader().loadDescriptors(FIXTURE));
        RepositoryFacts facts = new RepositoryFacts(
                "abc1234",
                List.of(
                        "magento/src/etc/app/code/Vendor/Module/Model/Foo.php",
                        "magento/src/etc/app/etc/config.php",
                        "magento/src/etc/bin/magento",
                        "magento/src/etc/composer.json"),
                Map.of());

        ProjectCapabilities selected = new ProjectSelector(registry).select(facts);

        assertThat(selected.repositoryPlugins()).containsExactly("php", "magento");
        assertThat(selected.detectionEvidence().get("magento"))
                .contains("root:magento/src/etc");
    }

    @Test
    void manual_type_bypasses_marker_detection_and_resolves_dependencies() throws Exception {
        PluginRegistry registry = new PluginRegistry(
                new PluginManifestLoader().loadDescriptors(FIXTURE));
        RepositoryFacts facts = new RepositoryFacts(
                "abc1234",
                List.of("magento/src/etc/app/code/Vendor/Module/Model/Foo.php"),
                Map.of(),
                "magento",
                "magento/src/etc");

        ProjectCapabilities selected = new ProjectSelector(registry).select(facts);

        assertThat(selected.repositoryPlugins()).containsExactly("php", "magento");
        assertThat(selected.detectionEvidence().get("magento")).containsExactly(
                "manual-project-type:magento", "root:magento/src/etc");
    }

    @Test
    void source_root_excludes_languages_and_files_outside_the_boundary() throws Exception {
        PluginRegistry registry = new PluginRegistry(
                new PluginManifestLoader().loadDescriptors(FIXTURE));
        List<String> paths = List.of(
                "app/etc/config.php",
                "bin/magento",
                "composer.json",
                "packages/store/src/Foo.php",
                "tools/Outside.java");
        Map<String, String> markerContents = Map.of(
                "composer.json", "{\"require\":{\"magento/framework\":\"*\"}}");

        ProjectCapabilities automatic = new ProjectSelector(registry).select(
                new RepositoryFacts(
                        "abc1234", paths, markerContents, null, "packages/store"));
        ProjectCapabilities explicit = new ProjectSelector(registry).select(
                new RepositoryFacts(
                        "abc1234", paths, markerContents, "magento", "packages/store"));

        assertThat(automatic.repositoryPlugins()).containsExactly("php");
        assertThat(automatic.filePlugins()).containsOnlyKeys(
                "packages/store/src/Foo.php");
        assertThat(explicit.repositoryPlugins()).containsExactly("php", "magento");
        assertThat(explicit.filePlugins()).containsOnlyKeys(
                "packages/store/src/Foo.php");
    }

    @Test
    void source_root_is_canonicalized_like_the_python_contract() {
        RepositoryFacts facts = new RepositoryFacts(
                "abc1234", List.of(), Map.of(), null, ".\\app/code");

        assertThat(facts.sourceRoot()).isEqualTo("app/code");
    }
}
