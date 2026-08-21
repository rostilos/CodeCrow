package org.rostilos.codecrow.plugins.rails;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.PluginCapability;
import org.rostilos.codecrow.plugins.DetectionRules;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginKind;
import org.rostilos.codecrow.plugins.PluginRegistry;
import org.rostilos.codecrow.plugins.ProjectSelector;
import org.rostilos.codecrow.plugins.RepositoryFacts;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class RailsPluginTest {
    @Test
    void packagedManifestLoadsThroughTheJavaContract() {
        var descriptor = new RailsPlugin().descriptor();
        assertThat(descriptor.id()).isEqualTo("rails");
        assertThat(descriptor.kind()).isEqualTo(PluginKind.FRAMEWORK);
        assertThat(descriptor.requires()).containsExactly("ruby");
        assertThat(descriptor.capabilities()).contains(
                PluginCapability.GRAPH, PluginCapability.INDEX, PluginCapability.VALIDATION);
        assertThat(descriptor.detection().alternatives()).hasSize(5);
    }

    @Test
    void engineDetectionKeepsAllEvidenceInsideOneNestedRoot() {
        var ruby = new PluginDescriptor(
                "ruby",
                PluginKind.LANGUAGE,
                List.of(),
                List.of(),
                new DetectionRules(
                        List.of(".rb"), List.of(), List.of(), List.of(), List.of()),
                Map.of());
        var selector = new ProjectSelector(new PluginRegistry(List.of(
                ruby,
                new RailsPlugin().descriptor())));

        var split = selector.select(new RepositoryFacts(
                "abc1234",
                List.of(
                        "a/example.gemspec",
                        "b/config/routes.rb",
                        "c/lib/example/engine.rb"),
                Map.of(
                        "c/lib/example/engine.rb",
                        "class Example < Rails::Engine\nend\n")));
        var coherent = selector.select(new RepositoryFacts(
                "abc1234",
                List.of(
                        "services/blog/blog.gemspec",
                        "services/blog/config/routes.rb",
                        "services/blog/lib/blog/engine.rb"),
                Map.of(
                        "services/blog/lib/blog/engine.rb",
                        "class Blog < Rails::Engine\nend\n")));
        var nestedButNotRootRelative = selector.select(new RepositoryFacts(
                "abc1234",
                List.of(
                        "services/blog/config/routes.rb",
                        "services/blog/nested/blog.gemspec",
                        "services/blog/vendor/lib/blog/engine.rb"),
                Map.of(
                        "services/blog/vendor/lib/blog/engine.rb",
                        "class Blog < Rails::Engine\nend\n")));

        assertThat(split.repositoryPlugins()).containsExactly("ruby");
        assertThat(coherent.repositoryPlugins()).containsExactly("ruby", "rails");
        assertThat(coherent.detectionEvidence().get("rails"))
                .contains("root:services/blog");
        assertThat(nestedButNotRootRelative.repositoryPlugins()).containsExactly("ruby");
    }
}
