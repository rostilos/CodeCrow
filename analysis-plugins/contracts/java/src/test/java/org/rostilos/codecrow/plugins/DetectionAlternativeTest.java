package org.rostilos.codecrow.plugins;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class DetectionAlternativeTest {

    @Test
    void prefixOrderingMatchesThePythonContract() {
        var shorter = new DetectionAlternative(
                List.of("composer.json"),
                List.of(),
                List.of("**/etc/module.xml"),
                List.of(),
                List.of());
        var longer = new DetectionAlternative(
                List.of("composer.json", "etc/module.xml", "registration.php"),
                List.of(),
                List.of(),
                List.of(),
                List.of());

        assertThat(shorter).isLessThan(longer);
    }
}
