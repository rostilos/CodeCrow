package org.rostilos.codecrow.plugins;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class PluginRuntimeTest {
    private static final String ZERO_FINGERPRINT = "sha256:" + "0".repeat(64);

    @Test
    void emptyRuntimePreservesFullFallbackDisposition() {
        var runtime = new PluginRuntime(List.of());

        assertThat(runtime.fileDisposition(
                "src/service.unknown", capabilities()))
                .isEqualTo(FileDisposition.FULL);
    }

    @Test
    void composes_file_policy_with_deterministic_precedence() {
        var runtime = new PluginRuntime(List.of(
                plugin("architecture", FileDisposition.ARCHITECTURE_ONLY),
                plugin("generated", FileDisposition.GENERATED)));
        var capabilities = capabilities("architecture", "generated");

        assertThat(runtime.fileDisposition("generated/code/Thing.php", capabilities))
                .isEqualTo(FileDisposition.GENERATED);
    }

    @Test
    void excluded_is_stronger_than_generated() {
        var runtime = new PluginRuntime(List.of(
                plugin("excluded", FileDisposition.EXCLUDED),
                plugin("generated", FileDisposition.GENERATED)));

        assertThat(runtime.fileDisposition(
                "dev/tests/Thing.php", capabilities("excluded", "generated")))
                .isEqualTo(FileDisposition.EXCLUDED);
    }

    @Test
    void abstaining_contributors_leave_the_fallback_disposition_full() {
        var runtime = new PluginRuntime(List.of(plugin("neutral", null)));

        assertThat(runtime.fileDisposition("src/Thing.php", capabilities("neutral")))
                .isEqualTo(FileDisposition.FULL);
    }

    @Test
    void explicit_plugin_failure_aborts_instead_of_silently_changing_policy() {
        var failed = new TestPlugin("broken", null) {
            @Override
            public PluginOutcome<FileDisposition> fileDisposition(String normalizedPath) {
                return PluginOutcome.failed(new PluginDiagnostic(
                        "broken-policy", "fixture failure", "broken"));
            }
        };
        var runtime = new PluginRuntime(List.of(failed));

        assertThatThrownBy(() -> runtime.fileDisposition(
                "src/Thing.php", capabilities("broken")))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("broken-policy");
    }

    private static TestPlugin plugin(String id, FileDisposition disposition) {
        return new TestPlugin(id, disposition);
    }

    private static ProjectCapabilities capabilities(String... ids) {
        return new ProjectCapabilities(
                List.of(ids), Map.of(), Map.of(), List.of(),
                ZERO_FINGERPRINT, ZERO_FINGERPRINT);
    }

    private static class TestPlugin implements FilePolicyPlugin {
        private final PluginDescriptor descriptor;
        private final FileDisposition disposition;

        private TestPlugin(String id, FileDisposition disposition) {
            this.disposition = disposition;
            descriptor = new PluginDescriptor(
                    id,
                    PluginKind.LANGUAGE,
                    List.of(),
                    List.of(PluginCapability.FILE_POLICY),
                    new DetectionRules(List.of(".test"), List.of(), List.of(), List.of(), List.of()),
                    Map.of());
        }

        @Override
        public PluginDescriptor descriptor() {
            return descriptor;
        }

        @Override
        public PluginOutcome<FileDisposition> fileDisposition(String normalizedPath) {
            return disposition == null
                    ? PluginOutcome.abstained()
                    : PluginOutcome.handled(disposition);
        }
    }
}
