package org.rostilos.codecrow.ragengine.source;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class RepositorySourceTreeIdentityTest {

    @TempDir
    Path repository;

    @Test
    void matchesTheRagConsumerCrossLanguageGoldenDigest()
            throws Exception {
        Files.createDirectories(repository.resolve("app"));
        Files.writeString(repository.resolve("app/Module.php"), "<?php\n");
        Files.writeString(repository.resolve("app.json"), "{\"name\":\"fixture\"}\n");
        Files.createDirectories(repository.resolve("src/Deep"));
        Files.writeString(repository.resolve("src/Deep/Value.php"), "value\n");
        Files.writeString(repository.resolve("café.txt"), "naïve\n");
        Files.createSymbolicLink(
                repository.resolve("latest"),
                Path.of("app/Module.php")
        );
        Files.createDirectories(repository.resolve(".git"));
        Files.writeString(repository.resolve(".git/internal"), "ignored\n");

        assertThat(RepositorySourceTreeIdentity.sha256(repository))
                .isEqualTo(
                        "927684e12c804a888d33a14a04c92291"
                                + "f329aff25941cfa13e5864fd4b15c411"
                );
    }

    @Test
    void changesWhenARepositoryFileChanges() throws Exception {
        Path source = repository.resolve("Example.php");
        Files.writeString(source, "first\n");
        String first = RepositorySourceTreeIdentity.sha256(repository);

        Files.writeString(source, "second\n");

        assertThat(RepositorySourceTreeIdentity.sha256(repository))
                .isNotEqualTo(first);
    }
}
