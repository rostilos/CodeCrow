package org.rostilos.codecrow.testsupport.offline;

import java.nio.file.Files;
import java.nio.file.Path;

final class SharedFixtureLocator {

    private SharedFixtureLocator() {
    }

    static Path locate(String workspaceRelativePath) {
        Path current = Path.of("").toAbsolutePath();
        while (current != null) {
            Path candidate = current.resolve(workspaceRelativePath);
            if (Files.isRegularFile(candidate)) {
                return candidate;
            }
            current = current.getParent();
        }
        throw new IllegalStateException("shared fixture not found: " + workspaceRelativePath);
    }
}
