package org.rostilos.codecrow.vcsclient.bitbucket.cloud;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipInputStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BitbucketGitArchiveDownloaderTest {
    private static final String ACCESS_TOKEN = "secret-oauth-token";

    @TempDir
    Path tempDirectory;

    @Test
    void createsDeterministicWorkingTreeArchiveWithoutLeakingTokenIntoArguments() throws Exception {
        RecordingRunner firstRunner = new RecordingRunner(false);
        RecordingRunner secondRunner = new RecordingRunner(false);
        Path firstArchive = tempDirectory.resolve("first.zip");
        Path secondArchive = tempDirectory.resolve("second.zip");

        long firstSize = new BitbucketGitArchiveDownloader(ACCESS_TOKEN, firstRunner)
                .download("perspective", "hofmanflowers", "develop", firstArchive);
        new BitbucketGitArchiveDownloader(ACCESS_TOKEN, secondRunner)
                .download("perspective", "hofmanflowers", "develop", secondArchive);

        assertThat(firstSize).isEqualTo(Files.size(firstArchive));
        assertThat(Files.readAllBytes(firstArchive)).isEqualTo(Files.readAllBytes(secondArchive));
        assertThat(zipEntries(firstArchive))
                .containsExactly(
                        "codecrow-repository-snapshot-000000000000/",
                        "codecrow-repository-snapshot-000000000000/app/",
                        "codecrow-repository-snapshot-000000000000/app/code/",
                        "codecrow-repository-snapshot-000000000000/app/code/Module.php",
                        "codecrow-repository-snapshot-000000000000/composer.json")
                .noneMatch(name -> name.startsWith(".git"));

        assertThat(firstRunner.commands).hasSize(4);
        assertThat(firstRunner.commands.get(1))
                .containsExactly(
                        "remote", "add", "origin",
                        "https://bitbucket.org/perspective/hofmanflowers.git");
        assertThat(firstRunner.commands.get(2))
                .containsExactly(
                        "fetch", "--quiet", "--no-tags", "--depth=1", "origin", "develop");
        assertThat(firstRunner.commands.toString()).doesNotContain(ACCESS_TOKEN);
        assertThat(firstRunner.environment.get("GIT_CONFIG_VALUE_0"))
                .startsWith("Authorization: Basic ")
                .doesNotContain(ACCESS_TOKEN);
        assertThat(firstRunner.environment.get("GIT_TERMINAL_PROMPT")).isEqualTo("0");
    }

    @Test
    void deletesPartialArchiveAndKeepsSecretOutOfFailureMessage() {
        Path archive = tempDirectory.resolve("failed.zip");

        assertThatThrownBy(() -> new BitbucketGitArchiveDownloader(
                ACCESS_TOKEN, new RecordingRunner(true))
                .download("perspective", "hofmanflowers", "develop", archive))
                .isInstanceOf(IOException.class)
                .hasMessage("simulated git failure")
                .hasMessageNotContaining(ACCESS_TOKEN);

        assertThat(archive).doesNotExist();
    }

    @Test
    void rejectsOptionLikeRefBeforeStartingGit() {
        RecordingRunner runner = new RecordingRunner(false);

        assertThatThrownBy(() -> new BitbucketGitArchiveDownloader(ACCESS_TOKEN, runner)
                .download("perspective", "hofmanflowers", "--upload-pack=evil", tempDirectory.resolve("bad.zip")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("option-like");

        assertThat(runner.commands).isEmpty();
    }

    private static List<String> zipEntries(Path archive) throws IOException {
        List<String> entries = new ArrayList<>();
        try (ZipInputStream zip = new ZipInputStream(Files.newInputStream(archive))) {
            for (var entry = zip.getNextEntry(); entry != null; entry = zip.getNextEntry()) {
                entries.add(entry.getName());
            }
        }
        return entries;
    }

    private static final class RecordingRunner
            implements BitbucketGitArchiveDownloader.GitCommandRunner {
        private final boolean failOnFetch;
        private final List<List<String>> commands = new ArrayList<>();
        private Map<String, String> environment = Map.of();

        private RecordingRunner(boolean failOnFetch) {
            this.failOnFetch = failOnFetch;
        }

        @Override
        public void run(
                Path workingDirectory,
                Map<String, String> environment,
                List<String> arguments
        ) throws IOException {
            commands.add(List.copyOf(arguments));
            this.environment = new LinkedHashMap<>(environment);
            if (failOnFetch && arguments.get(0).equals("fetch")) {
                throw new IOException("simulated git failure");
            }
            if (arguments.get(0).equals("checkout")) {
                Files.createDirectories(workingDirectory.resolve(".git"));
                Files.writeString(
                        workingDirectory.resolve(".git/config"), "ignored", StandardCharsets.UTF_8);
                Files.createDirectories(workingDirectory.resolve("app/code"));
                Files.writeString(
                        workingDirectory.resolve("app/code/Module.php"),
                        "<?php final class Module {}",
                        StandardCharsets.UTF_8);
                Files.writeString(
                        workingDirectory.resolve("composer.json"),
                        "{\"name\":\"fixture\"}",
                        StandardCharsets.UTF_8);
            }
        }
    }
}
