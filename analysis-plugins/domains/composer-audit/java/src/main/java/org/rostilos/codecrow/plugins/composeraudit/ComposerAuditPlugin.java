package org.rostilos.codecrow.plugins.composeraudit;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.rostilos.codecrow.plugins.PluginDescriptor;
import org.rostilos.codecrow.plugins.PluginManifestLoader;
import org.rostilos.codecrow.plugins.ReviewCheckPlugin;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.TreeSet;

public final class ComposerAuditPlugin implements ReviewCheckPlugin {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final int MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
    private final PluginDescriptor descriptor;
    private final URI endpoint;
    private final HttpClient client;

    public ComposerAuditPlugin() {
        this(configuredEndpoint());
    }

    private static URI configuredEndpoint() {
        try {
            return URI.create(System.getenv().getOrDefault("CODECROW_COMPOSER_AUDIT_URL", "http://composer-audit:8090/audit"));
        } catch (IllegalArgumentException invalid) {
            return null; // Invalid optional worker configuration is reported when a check is attempted.
        }
    }

    ComposerAuditPlugin(URI endpoint) {
        this.endpoint = endpoint;
        client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
        try (var input = getClass().getResourceAsStream(
                "/META-INF/codecrow/plugins/composer-audit/plugin.json")) {
            descriptor = new PluginManifestLoader().loadDescriptor(input);
        } catch (IOException exception) {
            throw new IllegalStateException("Cannot load Composer audit descriptor", exception);
        }
    }

    @Override
    public PluginDescriptor descriptor() { return descriptor; }

    @Override
    public boolean appliesTo(List<String> changedPaths) { return !composerRoots(changedPaths).isEmpty(); }

    @Override
    public String findingKey(Finding finding) {
        String root = finding.file().substring(0, finding.file().lastIndexOf('/') + 1);
        return root + "\0" + finding.title();
    }

    private static TreeSet<String> composerRoots(List<String> changedPaths) {
        TreeSet<String> roots = new TreeSet<>();
        for (String path : changedPaths) {
            if (path == null || path.startsWith("/") || path.contains("\\")
                    || List.of(path.split("/", -1)).stream().anyMatch(p -> p.equals("..") || p.isEmpty())) continue;
            if (path.equals("composer.json") || path.equals("composer.lock")
                    || path.endsWith("/composer.json") || path.endsWith("/composer.lock")) {
                roots.add(path.substring(0, path.lastIndexOf('/') + 1));
            }
        }
        return roots;
    }

    @Override
    public Result check(Context context) throws InterruptedException {
        var roots = composerRoots(context.changedPaths());
        if (roots.isEmpty()) return new Result(List.of(), List.of(), true);
        if (context.baseRevision() == null || context.baseRevision().isBlank()
                || context.headRevision() == null || context.headRevision().isBlank()) {
            return new Result(List.of(), List.of("comparison-unavailable: pinned PR revisions are missing"), false);
        }
        List<Finding> findings = new ArrayList<>();
        List<String> diagnostics = new ArrayList<>();
        boolean complete = true;
        long deadline = System.nanoTime() + Duration.ofMinutes(3).toNanos();
        for (String root : roots) {
            if (System.nanoTime() >= deadline) {
                diagnostics.add("time-limit: remaining Composer roots were not audited");
                complete = false;
                break;
            }
            try {
                var payload = new LinkedHashMap<String, String>();
                payload.put("path", root);
                payload.put("headManifest", context.source().read(context.headRevision(), root + "composer.json"));
                payload.put("headLock", context.source().read(context.headRevision(), root + "composer.lock"));
                payload.put("baseManifest", context.source().read(context.baseRevision(), root + "composer.json"));
                payload.put("baseLock", context.source().read(context.baseRevision(), root + "composer.lock"));
                try {
                    payload.put("auth", context.source().read(context.headRevision(), root + "auth.json"));
                } catch (IOException unavailableAuth) {
                    diagnostics.add("auth-unavailable: repository auth.json could not be read; Composer may use inline credentials");
                }
                HttpRequest request = HttpRequest.newBuilder(endpoint)
                        .timeout(Duration.ofNanos(Math.max(1, deadline - System.nanoTime())))
                        .header("Content-Type", "application/json")
                        .POST(HttpRequest.BodyPublishers.ofByteArray(JSON.writeValueAsBytes(payload))).build();
                // No transport retry: one sandbox invocation per root in this review attempt.
                // The worker bounds its response; buffering keeps the HTTP timeout active through body receipt.
                var response = client.send(request, HttpResponse.BodyHandlers.ofByteArray());
                if (response.statusCode() != 200 || response.body().length > MAX_RESPONSE_BYTES) {
                    throw new IOException("Audit sandbox did not return a bounded result");
                }
                Result result = JSON.readValue(response.body(), Result.class);
                findings.addAll(result.findings());
                diagnostics.addAll(result.diagnostics());
                complete &= result.complete();
            } catch (IOException | RuntimeException unavailable) {
                // Exception text can contain provider URLs or credential-bearing Composer output.
                diagnostics.add("audit-unavailable: a Composer root could not be audited; core review continues");
                complete = false;
            }
        }
        return new Result(findings, diagnostics, complete);
    }
}
