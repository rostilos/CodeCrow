package org.rostilos.codecrow.plugins;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class PluginRegistryTest {
    private static final Path FIXTURE = Path.of(
            System.getProperty("codecrow.plugin.fixtures"), "plugins-valid.json");

    private final PluginManifestLoader loader = new PluginManifestLoader();

    @Test
    void empty_registry_is_a_valid_runtime_configuration() {
        PluginRegistry registry = new PluginRegistry(List.of());

        assertThat(registry.orderedIds()).isEmpty();
        assertThat(registry.descriptors()).isEmpty();
    }

    @Test
    void sharedFixtureResolvesDependenciesBeforeFramework() throws Exception {
        PluginRegistry registry = new PluginRegistry(loader.loadDescriptors(FIXTURE));

        assertThat(registry.orderedIds()).containsExactly("php", "magento");
        assertThat(registry.resolve(List.of("magento")))
                .extracting(PluginDescriptor::id)
                .containsExactly("php", "magento");
        assertThat(registry.fingerprint()).isEqualTo(
                "sha256:b3a63c5e893ce3efa3b0fb35a8d2b4862f48c58fb7d366c0253d55262dbab676");
        assertThat(registry.fingerprintFor(List.of("php"))).isEqualTo(
                "sha256:def383c4fbe4b426bdd55e0bdac4a067ee0789603ba60bab530f8bfa61b27f51");
        assertThat(registry.fingerprintFor(List.of("magento")))
                .isEqualTo(registry.fingerprint());
    }

    @Test
    void registrationOrderDoesNotChangeOrderOrFingerprint() throws Exception {
        List<PluginDescriptor> descriptors = loader.loadDescriptors(FIXTURE);
        PluginRegistry first = new PluginRegistry(descriptors);
        PluginRegistry reversed = new PluginRegistry(List.of(descriptors.get(1), descriptors.get(0)));

        assertThat(reversed.orderedIds()).isEqualTo(first.orderedIds());
        assertThat(reversed.fingerprint()).isEqualTo(first.fingerprint());
    }

    @Test
    void duplicateMissingAndCyclicDependenciesAreRejected() {
        PluginDescriptor php = descriptor("php", PluginKind.LANGUAGE, List.of());
        assertThatThrownBy(() -> new PluginRegistry(List.of(php, php)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("duplicate plugin id");
        assertThatThrownBy(() -> new PluginRegistry(List.of(
                descriptor("magento", PluginKind.FRAMEWORK, List.of("php")))))
                .hasMessageContaining("missing plugins");
        assertThatThrownBy(() -> new PluginRegistry(List.of(
                descriptor("alpha", PluginKind.LANGUAGE, List.of("beta")),
                descriptor("beta", PluginKind.LANGUAGE, List.of("alpha")))))
                .hasMessageContaining("dependency cycle");
    }

    @Test
    void frameworkMustDependOnALanguagePlugin() {
        assertThatThrownBy(() -> new PluginRegistry(List.of(
                descriptor("framework", PluginKind.FRAMEWORK, List.of()))))
                .hasMessageContaining("must depend on a language");
    }

    @Test
    void domainPluginIsLanguageNeutral() {
        PluginRegistry registry = new PluginRegistry(List.of(
                descriptor("contracts", PluginKind.DOMAIN, List.of())));

        assertThat(registry.orderedIds()).containsExactly("contracts");
    }

    @Test
    void manifestRejectsApplicationReleaseFieldsAndUnsortedCapabilities() throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        ArrayNode root = (ArrayNode) mapper.readTree(FIXTURE.toFile());
        ObjectNode php = (ObjectNode) root.get(1);
        php.put("version", "1");
        assertThatThrownBy(() -> loader.parseDescriptor(php))
                .hasMessageContaining("unknown=[version]");

        php.remove("version");
        ArrayNode capabilities = mapper.createArrayNode().add("syntax").add("context");
        php.set("capabilities", capabilities);
        assertThatThrownBy(() -> loader.parseDescriptor(php))
                .hasMessageContaining("unique and sorted");
    }

    @Test
    void capabilityLookupUsesDependencyOrder() throws Exception {
        PluginRegistry registry = new PluginRegistry(loader.loadDescriptors(FIXTURE));
        assertThat(registry.forCapability(PluginCapability.GRAPH, List.of("magento")))
                .extracting(PluginDescriptor::id)
                .containsExactly("php", "magento");
    }

    @Test
    void outcomeStatesAreUnambiguous() {
        assertThat(PluginOutcome.handled("value").status()).isEqualTo(OutcomeStatus.HANDLED);
        assertThat(PluginOutcome.abstained().status()).isEqualTo(OutcomeStatus.ABSTAINED);
        assertThat(PluginOutcome.failed(new PluginDiagnostic(
                "parse-failed", "invalid source", "php")).status()).isEqualTo(OutcomeStatus.FAILED);
        assertThatThrownBy(() -> new PluginOutcome<>(OutcomeStatus.HANDLED, null, null))
                .hasMessageContaining("handled outcome");
        assertThatThrownBy(() -> new PluginOutcome<>(OutcomeStatus.FAILED, null, null))
                .hasMessageContaining("failed outcome");
    }

    @Test
    void repositoryAndProjectFactsAreImmutableAndCanonical() {
        RepositoryFacts facts = new RepositoryFacts(
                "abc1234", List.of("app/code/A.php", "composer.json"), Map.of("composer.json", "{}"));
        assertThat(facts.markerContents()).containsOnlyKeys("composer.json");
        assertThatThrownBy(() -> facts.markerContents().put("other", "x"))
                .isInstanceOf(UnsupportedOperationException.class);

        ProjectCapabilities capabilities = new ProjectCapabilities(
                List.of("php", "magento"),
                Map.of("app/code/A.php", List.of("php")),
                Map.of("magento", List.of("composer.json")),
                List.of(),
                "sha256:" + "0".repeat(64));
        assertThat(capabilities.filePlugins().get("app/code/A.php")).containsExactly("php");
    }

    @Test
    void unknownRequestedPluginIsRejected() throws Exception {
        PluginRegistry registry = new PluginRegistry(loader.loadDescriptors(FIXTURE));
        assertThatThrownBy(() -> registry.resolve(List.of("missing")))
                .hasMessageContaining("unknown requested plugin");
    }

    private static PluginDescriptor descriptor(
            String id, PluginKind kind, List<String> requires) {
        return new PluginDescriptor(
                id,
                kind,
                requires,
                List.of(PluginCapability.SYNTAX),
                DetectionRules.empty(),
                Map.of());
    }
}
