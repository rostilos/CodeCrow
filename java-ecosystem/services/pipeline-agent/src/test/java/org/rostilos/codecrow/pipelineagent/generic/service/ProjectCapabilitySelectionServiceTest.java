package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.config.AnalysisProfileConfig;
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
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

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

    @Test
    void manual_project_type_is_authoritative_and_reads_no_markers() {
        var runtime = new PluginRuntime(List.of(new FixturePolicyPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = mock(VcsClient.class);

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("magento/src/etc/src/Thing.fixture"),
                new AnalysisProfileConfig(
                        "fixture-policy",
                        "magento/src/etc"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("fixture-policy");
        assertThat(plan.preliminaryCapabilities().detectionEvidence()
                .get("fixture-policy"))
                .containsExactly(
                        "manual-project-type:fixture-policy",
                        "root:magento/src/etc");
        verifyNoInteractions(vcsClient);
    }

    @Test
    void automatic_selection_reads_exact_markers_below_configured_source_root()
            throws Exception {
        var runtime = new PluginRuntime(List.of(new RootedMarkerPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = mock(VcsClient.class);
        when(vcsClient.getFileContent(
                "workspace",
                "repository",
                "magento/src/etc/framework.marker",
                "0123456789abcdef"))
                .thenReturn("framework=true");

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("magento/src/etc/src/Thing.fixture"),
                new AnalysisProfileConfig(null, "magento/src/etc"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("rooted-marker");
        assertThat(plan.preliminaryCapabilities().detectionEvidence()
                .get("rooted-marker"))
                .contains("root:magento/src/etc");
        verify(vcsClient).getFileContent(
                "workspace",
                "repository",
                "magento/src/etc/framework.marker",
                "0123456789abcdef");
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

    private static final class RootedMarkerPlugin implements FilePolicyPlugin {
        private final PluginDescriptor descriptor = new PluginDescriptor(
                "rooted-marker",
                PluginKind.LANGUAGE,
                List.of(),
                List.of(PluginCapability.FILE_POLICY),
                new DetectionRules(
                        List.of(),
                        List.of("framework.marker"),
                        List.of(),
                        List.of(),
                        List.of()),
                Map.of());

        @Override
        public PluginDescriptor descriptor() {
            return descriptor;
        }

        @Override
        public PluginOutcome<FileDisposition> fileDisposition(String normalizedPath) {
            return PluginOutcome.handled(FileDisposition.FULL);
        }
    }
}
