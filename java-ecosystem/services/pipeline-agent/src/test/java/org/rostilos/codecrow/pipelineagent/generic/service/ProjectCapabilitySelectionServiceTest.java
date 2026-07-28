package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.DetectionRules;
import org.rostilos.codecrow.plugins.FileDisposition;
import org.rostilos.codecrow.plugins.FilePolicyPlugin;
import org.rostilos.codecrow.plugins.PluginCapability;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginKind;
import org.rostilos.codecrow.plugins.PluginOutcome;
import org.rostilos.codecrow.plugins.PluginRuntime;
import org.rostilos.codecrow.vcsclient.VcsClient;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class ProjectCapabilitySelectionServiceTest {

    @Test
    void application_host_starts_with_no_plugin_implementations_on_its_classpath() {
        assertThat(new ProjectCapabilitySelectionService().registry().orderedIds()).isEmpty();
    }

    @Test
    void plugin_policy_filters_generated_paths_before_enrichment() {
        var runtime = new PluginRuntime(List.of(new FixturePolicyPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);

        var plan = service.plan(
                mock(VcsClient.class),
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("src/Thing.fixture", "generated/code/Proxy.fixture"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("fixture-policy");
        assertThat(plan.enrichmentPaths()).containsExactly("src/Thing.fixture");
    }

    private static final class FixturePolicyPlugin implements FilePolicyPlugin {
        private final PluginDescriptor descriptor = new PluginDescriptor(
                "fixture-policy",
                PluginKind.LANGUAGE,
                List.of(),
                List.of(PluginCapability.FILE_POLICY),
                new DetectionRules(
                        List.of(".fixture"), List.of(), List.of(), List.of(), List.of()),
                Map.of());

        @Override
        public PluginDescriptor descriptor() {
            return descriptor;
        }

        @Override
        public PluginOutcome<FileDisposition> fileDisposition(String normalizedPath) {
            return PluginOutcome.handled(
                    normalizedPath.startsWith("generated/")
                            ? FileDisposition.GENERATED
                            : FileDisposition.FULL);
        }
    }
}
