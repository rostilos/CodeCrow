package org.rostilos.codecrow.plugins;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class PluginGlobTest {
    private static final Path FIXTURE = Path.of(
            System.getProperty("codecrow.plugin.fixtures"), "plugin-globs.json");

    @Test
    void matchesTheSharedAnchoredProjection() throws Exception {
        List<GlobCase> cases = new ObjectMapper().readValue(
                Files.readString(FIXTURE),
                new TypeReference<>() {});

        assertThat(cases.stream()
                .map(value -> PluginGlob.matches(value.glob(), value.path()))
                .toList())
                .containsExactlyElementsOf(cases.stream().map(GlobCase::matches).toList());
    }

    private record GlobCase(String glob, String path, boolean matches) {
    }
}
