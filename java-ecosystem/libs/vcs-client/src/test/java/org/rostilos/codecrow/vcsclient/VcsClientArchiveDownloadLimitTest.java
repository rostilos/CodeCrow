package org.rostilos.codecrow.vcsclient;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class VcsClientArchiveDownloadLimitTest {

    @Test
    void boundedCopyStopsBeforeWritingPastLimit(@TempDir Path tempDir)
            throws Exception {
        byte[] archive = "0123456789abcdef".getBytes(StandardCharsets.UTF_8);
        Path target = tempDir.resolve("repository.zip");

        assertThatThrownBy(() -> VcsClient.copyRepositoryArchive(
                new ByteArrayInputStream(archive), target, 8))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("size limit");

        assertThat(Files.size(target)).isLessThanOrEqualTo(8);
    }

    @Test
    void boundedCopyReturnsObservedLength(@TempDir Path tempDir) throws Exception {
        byte[] archive = "archive".getBytes(StandardCharsets.UTF_8);
        Path target = tempDir.resolve("repository.zip");

        long written = VcsClient.copyRepositoryArchive(
                new ByteArrayInputStream(archive), target, archive.length);

        assertThat(written).isEqualTo(archive.length);
        assertThat(Files.readAllBytes(target)).isEqualTo(archive);
    }

    @Test
    void boundedOverloadFailsClosedWhenProviderOnlyImplementsLegacyWriter(
            @TempDir Path tempDir) {
        AtomicBoolean legacyWriterCalled = new AtomicBoolean();
        VcsClient legacyOnly = (VcsClient) Proxy.newProxyInstance(
                VcsClient.class.getClassLoader(),
                new Class<?>[]{VcsClient.class},
                (proxy, method, arguments) -> {
                    if (method.isDefault()) {
                        return InvocationHandler.invokeDefault(proxy, method, arguments);
                    }
                    if (method.getName().equals("downloadRepositoryArchiveToFile")
                            && method.getParameterCount() == 4) {
                        legacyWriterCalled.set(true);
                        return 0L;
                    }
                    throw new AssertionError("Unexpected VCS call: " + method);
                });

        assertThatThrownBy(() -> legacyOnly.downloadRepositoryArchiveToFile(
                "workspace",
                "repository",
                "a".repeat(40),
                tempDir.resolve("repository.zip"),
                8))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("Bounded");

        assertThat(legacyWriterCalled).isFalse();
    }
}
