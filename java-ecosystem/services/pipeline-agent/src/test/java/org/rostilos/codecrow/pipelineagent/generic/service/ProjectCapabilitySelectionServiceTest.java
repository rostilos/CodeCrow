package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.core.model.project.config.AnalysisProfileConfig;
import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.ContentMarker;
import org.rostilos.codecrow.plugins.ContentPatternMarker;
import org.rostilos.codecrow.plugins.DetectionAlternative;
import org.rostilos.codecrow.plugins.DetectionRules;
import org.rostilos.codecrow.plugins.FileDisposition;
import org.rostilos.codecrow.plugins.FilePolicyPlugin;
import org.rostilos.codecrow.plugins.PluginCapability;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginKind;
import org.rostilos.codecrow.plugins.PluginOutcome;
import org.rostilos.codecrow.plugins.PluginRuntime;
import org.rostilos.codecrow.vcsclient.VcsClient;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class ProjectCapabilitySelectionServiceTest {

    @Test
    void application_host_starts_with_no_plugin_implementations_on_its_classpath() {
        assertThat(new ProjectCapabilitySelectionService().registry().orderedIds()).isEmpty();
    }

    @Test
    void plugin_policy_filters_generated_paths_before_enrichment() throws Exception {
        var runtime = new PluginRuntime(List.of(new FixturePolicyPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = unavailableInventoryClient();

        var plan = service.plan(
                vcsClient,
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
        var vcsClient = unavailableInventoryClient();
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

    @Test
    void automatic_selection_reads_markers_below_inferred_nested_root()
            throws Exception {
        var runtime = new PluginRuntime(List.of(new RootedMarkerPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = unavailableInventoryClient();
        when(vcsClient.getFileContent(
                "workspace",
                "repository",
                "magento/src/framework.marker",
                "0123456789abcdef"))
                .thenReturn("framework=true");

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of(
                        "magento/src/framework.marker",
                        "magento/src/src/Thing.fixture"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("rooted-marker");
        assertThat(plan.preliminaryCapabilities().detectionEvidence()
                .get("rooted-marker"))
                .contains("root:magento/src");
        verify(vcsClient).getFileContent(
                "workspace",
                "repository",
                "magento/src/framework.marker",
                "0123456789abcdef");
    }

    @Test
    void complete_inventory_selects_nested_django_from_unchanged_path_patterns()
            throws Exception {
        var runtime = new PluginRuntime(List.of(
                languagePlugin("python", ".py"),
                djangoPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = mock(VcsClient.class);
        when(vcsClient.listRepositoryFiles(
                "workspace", "repository", "0123456789abcdef", 500_000))
                .thenReturn(List.of(
                        "services/store/manage.py",
                        "services/store/store/settings.py",
                        "services/store/store/urls.py",
                        "services/store/catalog/views.py",
                        "unrelated/tool.py"));

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("services/store/catalog/views.py"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("python", "django");
        assertThat(plan.preliminaryCapabilities().detectionEvidence().get("django"))
                .contains(
                        "root:services/store",
                        "file:services/store/manage.py",
                        "pattern:**/settings.py:services/store/store/settings.py",
                        "pattern:**/urls.py:services/store/store/urls.py");
        assertThat(plan.preliminaryCapabilities().filePlugins())
                .containsOnlyKeys("services/store/catalog/views.py")
                .containsEntry("services/store/catalog/views.py", List.of("python"));
        assertThat(plan.enrichmentPaths())
                .containsExactly("services/store/catalog/views.py");
    }

    @Test
    void complete_inventory_does_not_restore_a_deleted_changed_marker()
            throws Exception {
        var runtime = new PluginRuntime(List.of(new RootedMarkerPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = mock(VcsClient.class);
        when(vcsClient.listRepositoryFiles(
                "workspace", "repository", "0123456789abcdef", 500_000))
                .thenReturn(List.of("src/Thing.fixture"));

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("framework.marker", "src/Thing.fixture"));
        var enrichment = new PrEnrichmentDataDto(
                List.of(FileContentDto.skipped("framework.marker", "deleted")),
                List.of(),
                List.of(),
                PrEnrichmentDataDto.EnrichmentStats.empty());

        assertThat(plan.repositoryPaths()).containsExactly("src/Thing.fixture");
        assertThat(plan.preliminaryCapabilities().repositoryPlugins()).isEmpty();
        assertThat(service.complete(plan, enrichment).repositoryPlugins()).isEmpty();
        verify(vcsClient, org.mockito.Mockito.never()).getFileContent(
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.anyString());
    }

    @Test
    void complete_inventory_reads_only_existing_nested_quarkus_content_marker()
            throws Exception {
        var runtime = new PluginRuntime(List.of(
                languagePlugin("java", ".java"),
                quarkusPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = mock(VcsClient.class);
        when(vcsClient.listRepositoryFiles(
                "workspace", "repository", "0123456789abcdef", 500_000))
                .thenReturn(List.of(
                        "services/orders/pom.xml",
                        "services/orders/src/main/java/example/OrderResource.java",
                        "services/legacy/pom.xml"));
        when(vcsClient.getFileContent(
                "workspace", "repository", "services/orders/pom.xml", "0123456789abcdef"))
                .thenReturn("<groupId>io.quarkus</groupId>");
        when(vcsClient.getFileContent(
                "workspace", "repository", "services/legacy/pom.xml", "0123456789abcdef"))
                .thenReturn("<groupId>example</groupId>");

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("services/orders/src/main/java/example/OrderResource.java"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("java", "quarkus");
        assertThat(plan.preliminaryCapabilities().detectionEvidence().get("quarkus"))
                .contains("root:services/orders");
        assertThat(plan.preliminaryCapabilities().filePlugins())
                .containsOnlyKeys("services/orders/src/main/java/example/OrderResource.java");
    }

    @Test
    void many_repository_roots_do_not_starve_the_changed_path_root()
            throws Exception {
        var runtime = new PluginRuntime(List.of(
                languagePlugin("java", ".java"),
                quarkusPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = mock(VcsClient.class);
        var repositoryFiles = new ArrayList<String>();
        IntStream.range(0, 100)
                .mapToObj(index -> "service-%03d/pom.xml".formatted(index))
                .forEach(repositoryFiles::add);
        repositoryFiles.add("zz-target/pom.xml");
        repositoryFiles.add("zz-target/src/main/java/example/OrderResource.java");
        when(vcsClient.listRepositoryFiles(
                "workspace", "repository", "0123456789abcdef", 500_000))
                .thenReturn(repositoryFiles);
        when(vcsClient.getFileContent(
                "workspace", "repository", "zz-target/pom.xml", "0123456789abcdef"))
                .thenReturn("<groupId>io.quarkus</groupId>");

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("zz-target/src/main/java/example/OrderResource.java"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("java", "quarkus");
        assertThat(plan.markerContents())
                .containsEntry("zz-target/pom.xml", "<groupId>io.quarkus</groupId>");
        verify(vcsClient).getFileContent(
                "workspace", "repository", "zz-target/pom.xml", "0123456789abcdef");
        verify(vcsClient, times(64)).getFileContent(
                org.mockito.ArgumentMatchers.eq("workspace"),
                org.mockito.ArgumentMatchers.eq("repository"),
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.eq("0123456789abcdef"));
    }

    @Test
    void complete_inventory_selects_nested_rails_from_unchanged_markers()
            throws Exception {
        var runtime = new PluginRuntime(List.of(
                languagePlugin("ruby", ".rb"),
                railsPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = mock(VcsClient.class);
        when(vcsClient.listRepositoryFiles(
                "workspace", "repository", "0123456789abcdef", 500_000))
                .thenReturn(List.of(
                        "apps/billing/Gemfile",
                        "apps/billing/config/routes.rb",
                        "apps/billing/app/models/invoice.rb"));
        when(vcsClient.getFileContent(
                "workspace", "repository", "apps/billing/Gemfile", "0123456789abcdef"))
                .thenReturn("gem \"rails\"");

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("apps/billing/app/models/invoice.rb"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("ruby", "rails");
        assertThat(plan.preliminaryCapabilities().detectionEvidence().get("rails"))
                .contains("root:apps/billing");
        assertThat(plan.preliminaryCapabilities().filePlugins())
                .containsOnlyKeys("apps/billing/app/models/invoice.rb");
    }

    @Test
    void repository_inventory_failure_degrades_to_changed_file_detection()
            throws Exception {
        var runtime = new PluginRuntime(List.of(
                languagePlugin("python", ".py"),
                djangoPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = mock(VcsClient.class);
        when(vcsClient.listRepositoryFiles(
                "workspace", "repository", "0123456789abcdef", 500_000))
                .thenThrow(new IOException("tree temporarily unavailable"));

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("services/store/catalog/views.py"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("python");
        assertThat(plan.preliminaryCapabilities().filePlugins())
                .containsOnlyKeys("services/store/catalog/views.py");
        assertThat(plan.enrichmentPaths())
                .containsExactly("services/store/catalog/views.py");
    }

    @Test
    void degraded_inventory_retains_the_repository_root_quarkus_marker()
            throws Exception {
        var runtime = new PluginRuntime(List.of(
                languagePlugin("java", ".java"),
                quarkusPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = unavailableInventoryClient();
        when(vcsClient.getFileContent(
                "workspace", "repository", "pom.xml", "0123456789abcdef"))
                .thenReturn("<groupId>io.quarkus</groupId>");
        String deeplyNestedJava = String.join(
                "/",
                IntStream.range(0, 80)
                        .mapToObj(index -> "directory-%02d".formatted(index))
                        .toList()) + "/OrderResource.java";

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of(deeplyNestedJava));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("java", "quarkus");
        assertThat(plan.preliminaryCapabilities().detectionEvidence().get("quarkus"))
                .contains("root:.");
        verify(vcsClient).getFileContent(
                "workspace", "repository", "pom.xml", "0123456789abcdef");
    }

    @Test
    void degraded_inventory_retains_content_pattern_evidence_for_multiple_roots()
            throws Exception {
        var runtime = new PluginRuntime(List.of(
                languagePlugin("ruby", ".rb"),
                railsEnginePlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = unavailableInventoryClient();
        var changedFiles = List.of(
                "engines/accounts/config/routes.rb",
                "engines/accounts/accounts.gemspec",
                "engines/accounts/lib/accounts/engine.rb",
                "engines/billing/config/routes.rb",
                "engines/billing/billing.gemspec",
                "engines/billing/lib/billing/engine.rb");
        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                changedFiles);
        var enrichment = new PrEnrichmentDataDto(
                List.of(
                        FileContentDto.of(
                                "engines/billing/lib/billing/engine.rb",
                                "class BillingEngine < Rails::Engine; end"),
                        FileContentDto.of(
                                "engines/accounts/lib/accounts/engine.rb",
                                "class AccountsEngine < Rails::Engine; end")),
                List.of(),
                List.of(),
                PrEnrichmentDataDto.EnrichmentStats.empty());

        var capabilities = service.complete(plan, enrichment);

        assertThat(capabilities.repositoryPlugins())
                .containsExactly("ruby", "rails-engine");
        assertThat(capabilities.detectionEvidence().get("rails-engine"))
                .contains(
                        "root:engines/accounts",
                        "root:engines/billing",
                        "content-pattern:lib/**/engine.rb:engines/accounts/lib/accounts/engine.rb:Rails::Engine",
                        "content-pattern:lib/**/engine.rb:engines/billing/lib/billing/engine.rb:Rails::Engine");
    }

    @Test
    void automatic_selection_degrades_instead_of_failing_when_marker_file_budget_is_exceeded()
            throws Exception {
        var runtime = new PluginRuntime(List.of(new ManyMarkerPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = unavailableInventoryClient();

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("src/Thing.fixture"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins()).isEmpty();
        assertThat(plan.markerContents()).isEmpty();
        verify(vcsClient, times(64)).getFileContent(
                org.mockito.ArgumentMatchers.eq("workspace"),
                org.mockito.ArgumentMatchers.eq("repository"),
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.eq("0123456789abcdef"));
    }

    @Test
    void changed_marker_paths_are_prioritized_inside_the_bounded_schedule()
            throws Exception {
        var runtime = new PluginRuntime(List.of(new ManyMarkerPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = unavailableInventoryClient();
        when(vcsClient.getFileContent(
                "workspace",
                "repository",
                "marker-69.file",
                "0123456789abcdef"))
                .thenReturn("present");

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("marker-69.file", "src/Thing.fixture"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("many-markers");
        assertThat(plan.markerContents()).containsKey("marker-69.file");
    }

    @Test
    void automatic_selection_skips_oversized_marker_content_and_keeps_reading()
            throws Exception {
        var runtime = new PluginRuntime(List.of(new TwoMarkerPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = unavailableInventoryClient();
        when(vcsClient.getFileContent(
                "workspace", "repository", "a.marker", "0123456789abcdef"))
                .thenReturn("framework=true" + "x".repeat(300_000));
        when(vcsClient.getFileContent(
                "workspace", "repository", "b.marker", "0123456789abcdef"))
                .thenReturn("framework=true");

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("src/Thing.fixture"));

        assertThat(plan.markerContents())
                .containsOnlyKeys("b.marker")
                .containsEntry("b.marker", "framework=true");
        assertThat(plan.markerBytes()).isEqualTo("framework=true".length());
        assertThat(plan.preliminaryCapabilities().repositoryPlugins())
                .containsExactly("two-markers");
    }

    @Test
    void automatic_selection_degrades_when_an_optional_marker_cannot_be_read()
            throws Exception {
        var runtime = new PluginRuntime(List.of(new RootedMarkerPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = unavailableInventoryClient();
        when(vcsClient.getFileContent(
                "workspace",
                "repository",
                "framework.marker",
                "0123456789abcdef"))
                .thenThrow(new IOException("temporary marker read failure"));

        var plan = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("src/Thing.fixture"));

        assertThat(plan.preliminaryCapabilities().repositoryPlugins()).isEmpty();
        assertThat(plan.markerContents()).isEmpty();
    }

    @Test
    void completion_skips_pattern_content_that_exceeds_the_remaining_byte_budget()
            throws Exception {
        var runtime = new PluginRuntime(List.of(new PatternMarkerPlugin()));
        var service = new ProjectCapabilitySelectionService(runtime);
        var vcsClient = unavailableInventoryClient();
        var preliminary = service.plan(
                vcsClient,
                "workspace",
                "repository",
                "0123456789abcdef",
                List.of("src/Thing.fixture"));
        var marker = new ContentPatternMarker("**/*.fixture", "framework=true");
        var nearlyFull = new ProjectCapabilitySelectionService.SelectionPlan(
                preliminary.commit(),
                preliminary.repositoryPaths(),
                Map.of("seed.marker", "x"),
                List.of(marker),
                262_140,
                preliminary.preliminaryCapabilities(),
                preliminary.enrichmentPaths(),
                preliminary.projectType(),
                preliminary.sourceRoot(),
                preliminary.completeRepositoryInventory());
        var enrichment = new PrEnrichmentDataDto(
                List.of(FileContentDto.of("src/Thing.fixture", "framework=true")),
                List.of(),
                List.of(),
                PrEnrichmentDataDto.EnrichmentStats.empty());

        var capabilities = service.complete(nearlyFull, enrichment);

        assertThat(capabilities.repositoryPlugins()).isEmpty();
    }

    private static VcsClient unavailableInventoryClient() throws IOException {
        var vcsClient = mock(VcsClient.class);
        when(vcsClient.listRepositoryFiles(
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.anyInt()))
                .thenThrow(new UnsupportedOperationException(
                        "repository inventory unavailable"));
        return vcsClient;
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

    private static CodeCrowPlugin languagePlugin(String id, String extension) {
        return descriptorPlugin(new PluginDescriptor(
                id,
                PluginKind.LANGUAGE,
                List.of(),
                List.of(PluginCapability.CONTEXT),
                new DetectionRules(
                        List.of(extension), List.of(), List.of(), List.of(), List.of()),
                Map.of()));
    }

    private static CodeCrowPlugin djangoPlugin() {
        return descriptorPlugin(new PluginDescriptor(
                "django",
                PluginKind.FRAMEWORK,
                List.of("python"),
                List.of(PluginCapability.CONTEXT),
                new DetectionRules(
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(new DetectionAlternative(
                                List.of("manage.py"),
                                List.of(),
                                List.of("**/settings.py", "**/urls.py"),
                                List.of(),
                                List.of()))),
                Map.of()));
    }

    private static CodeCrowPlugin quarkusPlugin() {
        return descriptorPlugin(new PluginDescriptor(
                "quarkus",
                PluginKind.FRAMEWORK,
                List.of("java"),
                List.of(PluginCapability.CONTEXT),
                new DetectionRules(
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(new DetectionAlternative(
                                List.of(),
                                List.of(),
                                List.of(),
                                List.of(),
                                List.of(new ContentMarker("pom.xml", "io.quarkus"))))),
                Map.of()));
    }

    private static CodeCrowPlugin railsPlugin() {
        return descriptorPlugin(new PluginDescriptor(
                "rails",
                PluginKind.FRAMEWORK,
                List.of("ruby"),
                List.of(PluginCapability.CONTEXT),
                new DetectionRules(
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(new DetectionAlternative(
                                List.of("Gemfile", "config/routes.rb"),
                                List.of(),
                                List.of(),
                                List.of(),
                                List.of(new ContentMarker("Gemfile", "gem \"rails\""))))),
                Map.of()));
    }

    private static CodeCrowPlugin railsEnginePlugin() {
        return descriptorPlugin(new PluginDescriptor(
                "rails-engine",
                PluginKind.FRAMEWORK,
                List.of("ruby"),
                List.of(PluginCapability.CONTEXT),
                new DetectionRules(
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(new DetectionAlternative(
                                List.of("config/routes.rb"),
                                List.of(),
                                List.of("*.gemspec"),
                                List.of(),
                                List.of(),
                                List.of(new ContentPatternMarker(
                                        "lib/**/engine.rb",
                                        "Rails::Engine"))))),
                Map.of()));
    }

    private static CodeCrowPlugin descriptorPlugin(PluginDescriptor descriptor) {
        return () -> descriptor;
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

    private static final class ManyMarkerPlugin implements FilePolicyPlugin {
        private final PluginDescriptor descriptor = new PluginDescriptor(
                "many-markers",
                PluginKind.LANGUAGE,
                List.of(),
                List.of(PluginCapability.FILE_POLICY),
                new DetectionRules(
                        List.of(),
                        List.of(),
                        IntStream.range(0, 70)
                                .mapToObj(index -> "marker-%02d.file".formatted(index))
                                .toList(),
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

    private static final class TwoMarkerPlugin implements FilePolicyPlugin {
        private final PluginDescriptor descriptor = new PluginDescriptor(
                "two-markers",
                PluginKind.LANGUAGE,
                List.of(),
                List.of(PluginCapability.FILE_POLICY),
                new DetectionRules(
                        List.of(),
                        List.of("a.marker", "b.marker"),
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

    private static final class PatternMarkerPlugin implements FilePolicyPlugin {
        private final PluginDescriptor descriptor = new PluginDescriptor(
                "pattern-marker",
                PluginKind.LANGUAGE,
                List.of(),
                List.of(PluginCapability.FILE_POLICY),
                new DetectionRules(
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(),
                        List.of(new DetectionAlternative(
                                List.of(),
                                List.of(),
                                List.of(),
                                List.of(),
                                List.of(),
                                List.of(new ContentPatternMarker(
                                        "**/*.fixture",
                                        "framework=true"))))),
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
