package org.rostilos.codecrow.ragengine.service;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

/** Combines RAG runtime content identity with material project index inputs. */
@Service
public class RagRepresentationIdentityService {
    private static final Logger log = LoggerFactory.getLogger(
            RagRepresentationIdentityService.class);

    private final RagPipelineClient pipelineClient;
    private volatile String cachedRuntimeIdentity;
    private volatile long cachedUntilNanos;

    public RagRepresentationIdentityService(RagPipelineClient pipelineClient) {
        this.pipelineClient = pipelineClient;
    }

    /** Identity lookup is optional enrichment and therefore fails open. */
    public Optional<String> currentProjectFingerprint(Project project) {
        return currentRuntimeIdentity().map(identity -> combine(identity, project));
    }

    /** Fetch once per reconciliation pass; the successful runtime value is briefly cached. */
    public Optional<String> currentRuntimeIdentity() {
        try {
            return Optional.of(resolveRuntimeIdentity());
        } catch (Exception unavailable) {
            log.debug(
                    "RAG representation identity is temporarily unavailable: {}",
                    unavailable.getMessage());
            return Optional.empty();
        }
    }

    public String projectFingerprint(String runtimeIdentity, Project project) {
        return combine(runtimeIdentity, project);
    }

    private String resolveRuntimeIdentity() throws Exception {
        long now = System.nanoTime();
        String cached = cachedRuntimeIdentity;
        if (cached != null && now < cachedUntilNanos) {
            return cached;
        }
        synchronized (this) {
            now = System.nanoTime();
            if (cachedRuntimeIdentity != null && now < cachedUntilNanos) {
                return cachedRuntimeIdentity;
            }
            String resolved = pipelineClient
                    .getCurrentRepresentationIdentity().identity();
            cachedRuntimeIdentity = resolved;
            cachedUntilNanos = now + TimeUnit.SECONDS.toNanos(60);
            return resolved;
        }
    }

    static String combine(String runtimeIdentity, Project project) {
        if (runtimeIdentity == null || runtimeIdentity.isBlank()) {
            throw new IllegalArgumentException("runtimeIdentity is required");
        }
        MessageDigest digest = sha256();
        add(digest, "runtime", runtimeIdentity.trim());
        var config = project != null ? project.getEffectiveConfig() : null;
        var rag = config != null ? config.ragConfig() : null;
        addAll(digest, "include", rag != null ? rag.includePatterns() : null);
        addAll(digest, "exclude", rag != null ? rag.excludePatterns() : null);
        var profile = config != null ? config.analysisProfile() : null;
        add(digest, "projectType", profile != null ? profile.projectType() : null);
        add(digest, "sourceRoot", profile != null ? profile.sourceRoot() : null);
        return "sha256:" + HexFormat.of().formatHex(digest.digest());
    }

    private static void addAll(
            MessageDigest digest,
            String name,
            List<String> values) {
        List<String> normalized = new ArrayList<>();
        if (values != null) {
            values.stream()
                    .filter(value -> value != null && !value.isBlank())
                    .map(String::trim)
                    .distinct()
                    .sorted(Comparator.naturalOrder())
                    .forEach(normalized::add);
        }
        add(digest, name + ".count", String.valueOf(normalized.size()));
        for (String value : normalized) {
            add(digest, name, value);
        }
    }

    private static void add(MessageDigest digest, String name, String value) {
        byte[] key = name.getBytes(StandardCharsets.UTF_8);
        byte[] content = (value != null ? value.trim() : "")
                .getBytes(StandardCharsets.UTF_8);
        digest.update(ByteBuffer.allocate(Integer.BYTES).putInt(key.length).array());
        digest.update(key);
        digest.update(ByteBuffer.allocate(Integer.BYTES).putInt(content.length).array());
        digest.update(content);
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }
}
