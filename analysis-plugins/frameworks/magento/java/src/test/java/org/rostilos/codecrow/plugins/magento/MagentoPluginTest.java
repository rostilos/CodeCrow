package org.rostilos.codecrow.plugins.magento;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.CodeCrowPlugin;
import org.rostilos.codecrow.plugins.DetectionRules;
import org.rostilos.codecrow.plugins.FileDisposition;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginKind;
import org.rostilos.codecrow.plugins.PluginRuntime;
import org.rostilos.codecrow.plugins.ProjectCapabilities;

import java.io.InputStream;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Objects;

import static org.assertj.core.api.Assertions.assertThat;

class MagentoPluginTest {
    private static final String ZERO_FINGERPRINT = "sha256:" + "0".repeat(64);

    @Test
    void packagedManifestLoadsThroughTheJavaContract() {
        var descriptor = new MagentoPlugin().descriptor();

        assertThat(descriptor.id()).isEqualTo("magento");
        assertThat(descriptor.kind()).isEqualTo(PluginKind.FRAMEWORK);
        assertThat(descriptor.requires()).containsExactly("php");
        assertThat(descriptor.detection().alternatives()).hasSize(7);
    }

    @Test
    void directPolicyMatchesTheSharedCrossRuntimeVectors() throws Exception {
        var plugin = new MagentoPlugin();
        var vectors = vectors();

        assertThat(vectors.policyCases()).isNotEmpty();
        for (PolicyCase testCase : vectors.policyCases()) {
            assertThat(plugin.fileDisposition(testCase.path()).value())
                    .as("%s: %s", testCase.name(), testCase.path())
                    .isEqualTo(disposition(testCase.expected()));
        }
    }

    @Test
    void runtimeRootScopingMatchesTheSharedCrossRuntimeVectors() throws Exception {
        var magento = new MagentoPlugin();
        var runtime = new PluginRuntime(List.of(phpPlugin(), magento));
        var vectors = vectors();

        assertThat(vectors.runtimeCases()).isNotEmpty();
        for (RuntimeCase testCase : vectors.runtimeCases()) {
            var capabilities = new ProjectCapabilities(
                    List.of("php", "magento"),
                    Map.of(),
                    Map.of("magento", testCase.evidence()),
                    List.of(),
                    ZERO_FINGERPRINT,
                    ZERO_FINGERPRINT);

            assertThat(runtime.fileDisposition(testCase.path(), capabilities))
                    .as("%s: %s", testCase.name(), testCase.path())
                    .isEqualTo(disposition(testCase.expected()));
        }
    }

    private static PolicyVectors vectors() throws Exception {
        try (InputStream input = Objects.requireNonNull(
                MagentoPluginTest.class.getResourceAsStream("/magento-file-policy.json"),
                "shared Magento file-policy fixture is missing")) {
            return new ObjectMapper().readValue(input, PolicyVectors.class);
        }
    }

    private static FileDisposition disposition(String value) {
        return Arrays.stream(FileDisposition.values())
                .filter(candidate -> candidate.value().equals(value))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException(
                        "unknown fixture file disposition: " + value));
    }

    private static CodeCrowPlugin phpPlugin() {
        PluginDescriptor descriptor = new PluginDescriptor(
                "php",
                PluginKind.LANGUAGE,
                List.of(),
                List.of(),
                DetectionRules.empty(),
                Map.of());
        return () -> descriptor;
    }

    private record PolicyVectors(
            List<PolicyCase> policyCases,
            List<RuntimeCase> runtimeCases) {
    }

    private record PolicyCase(String name, String path, String expected) {
    }

    private record RuntimeCase(
            String name,
            String path,
            List<String> evidence,
            String expected) {
    }
}
