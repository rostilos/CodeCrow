package org.rostilos.codecrow.plugins.composeraudit;

import com.sun.net.httpserver.HttpServer;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.plugins.ReviewCheckPlugin;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;

class ComposerAuditPluginTest {
    @Test
    void nonComposerChangesAndMissingRevisionDoNotReadSources() throws Exception {
        var plugin = new ComposerAuditPlugin(URI.create("http://127.0.0.1:1/audit"));
        assertThat(plugin.descriptor().id()).isEqualTo("composer-audit");
        ReviewCheckPlugin.SourceReader forbidden = (revision, path) -> { throw new AssertionError("Unexpected read"); };
        assertThat(plugin.check(new ReviewCheckPlugin.Context("base", "head", List.of("main.php"), forbidden)).findings()).isEmpty();
        assertThat(plugin.check(new ReviewCheckPlugin.Context(null, "head", List.of("composer.lock"), forbidden)).diagnostics())
                .anyMatch(message -> message.contains("comparison-unavailable"));
    }

    @Test
    void eachRootUsesExactBaseAndHeadAndMissingAuthDoesNotBlockIt() throws Exception {
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        var bodies = new ArrayList<String>();
        server.createContext("/audit", exchange -> {
            bodies.add(new String(exchange.getRequestBody().readAllBytes(), java.nio.charset.StandardCharsets.UTF_8));
            byte[] response = "{\"findings\":[],\"diagnostics\":[\"audited\"],\"complete\":true}".getBytes();
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            var plugin = new ComposerAuditPlugin(URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/audit"));
            var reads = new ArrayList<String>();
            var result = plugin.check(new ReviewCheckPlugin.Context("base", "head",
                    List.of("app/composer.lock", "app/composer.json", "other/composer.json"), (revision, path) -> {
                        reads.add(revision + ":" + path);
                        if (path.endsWith("auth.json")) throw new IOException("credential-bearing error");
                        return revision;
                    }));
            assertThat(bodies).hasSize(2);
            assertThat(result.complete()).isTrue();
            assertThat(reads).contains("base:app/composer.lock", "head:app/composer.lock", "head:app/auth.json");
            assertThat(new ObjectMapper().readTree(bodies.get(0)).get("baseLock").asText()).isEqualTo("base");
            assertThat(result.diagnostics()).contains("audited").noneMatch(message -> message.contains("credential-bearing"));
        } finally { server.stop(0); }
    }

    @Test
    void unavailableSandboxDoesNotThrowOrRetry() throws Exception {
        var calls = new AtomicInteger();
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/audit", exchange -> {
            calls.incrementAndGet();
            exchange.getRequestBody().readAllBytes();
            exchange.sendResponseHeaders(503, -1);
            exchange.close();
        });
        server.start();
        try {
            var plugin = new ComposerAuditPlugin(URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/audit"));
            var result = plugin.check(new ReviewCheckPlugin.Context("base", "head", List.of("composer.json"), (r, p) -> "{}"));
            assertThat(calls).hasValue(1);
            assertThat(result.complete()).isFalse();
            assertThat(result.findings()).isEmpty();
            assertThat(result.diagnostics()).anyMatch(message -> message.contains("audit-unavailable"));
        } finally { server.stop(0); }
    }

    @Test
    void advisoryIdentitySurvivesManifestLockAnchorChangesButSeparatesComposerRoots() {
        var plugin = new ComposerAuditPlugin(URI.create("http://127.0.0.1:1/audit"));
        var manifest = new ReviewCheckPlugin.Finding("app/composer.json", 10, "HIGH", "vendor/package: TEST-001", "old", null, null);
        var lock = new ReviewCheckPlugin.Finding("app/composer.lock", 500, "HIGH", manifest.title(), "new", null, null);
        var other = new ReviewCheckPlugin.Finding("other/composer.lock", 500, "HIGH", manifest.title(), "new", null, null);
        assertThat(plugin.findingKey(manifest)).isEqualTo(plugin.findingKey(lock)).isNotEqualTo(plugin.findingKey(other));
    }
}
