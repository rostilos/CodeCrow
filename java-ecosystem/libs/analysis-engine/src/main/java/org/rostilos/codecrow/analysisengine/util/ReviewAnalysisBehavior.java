package org.rostilos.codecrow.analysisengine.util;

import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.project.Project;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Map;
import java.util.TreeMap;

/**
 * Current review-affecting behavior identity.
 *
 * <p>Change {@link #CONTRACT} whenever a production change can alter discovery,
 * evidence selection, verification, or publication. The resulting digest is
 * included in both the review fingerprint and the durable analysis identity,
 * preventing an old same-head result from masking the new behavior.</p>
 */
public final class ReviewAnalysisBehavior {

    public static final String CONTRACT =
            "current-head-manifest+hybrid-planning+exact-context+targeted-cross-file"
                    + "+verification-wave+deterministic-renderer@2026-08-13";

    public static final String DIGEST = sha256(CONTRACT);

    private ReviewAnalysisBehavior() {
    }

    /**
     * Combines the deployed engine contract with review-affecting project and
     * model configuration. Credentials, PR contents, and run history are
     * deliberately excluded: the digest is stable across delivery retries.
     */
    public static String digestFor(
            Project project,
            AIConnection aiConnection,
            String vcsProvider) {
        var config = project.getEffectiveConfig();
        TreeMap<String, String> inputs = new TreeMap<>();
        inputs.put("engine", DIGEST);
        inputs.put("aiProvider", value(aiConnection.getProviderKey()));
        inputs.put("aiModel", value(aiConnection.getAiModel()));
        inputs.put("aiBaseUrl", value(aiConnection.getBaseUrl()));
        inputs.put("aiCustomParameters", value(aiConnection.getCustomParameters()));
        inputs.put("maxTokens", Integer.toString(config.maxAnalysisTokenLimit()));
        inputs.put("useLocalMcp", "true");
        inputs.put("useMcpTools", Boolean.toString(config.useMcpTools()));
        inputs.put("ragConfig", value(config.ragConfig()));
        inputs.put("analysisScope", value(config.analysisScope()));
        inputs.put("analysisLimits", value(config.analysisLimits()));
        inputs.put("taskContextEnabled", Boolean.toString(config.isTaskContextAnalysisEnabled()));
        inputs.put("projectRules", value(config.getProjectRulesConfig().toEnabledRulesJson()));
        inputs.put("vcsProvider", value(vcsProvider));
        return digest(inputs);
    }

    public static String digestFor(AiAnalysisRequest request) {
        if (request.getAnalysisBehaviorDigest() != null
                && !request.getAnalysisBehaviorDigest().isBlank()) {
            return request.getAnalysisBehaviorDigest();
        }
        TreeMap<String, String> inputs = new TreeMap<>();
        inputs.put("engine", DIGEST);
        inputs.put("aiProvider", value(request.getAiProvider()));
        inputs.put("aiModel", value(request.getAiModel()));
        inputs.put("aiBaseUrl", value(request.getAiBaseUrl()));
        inputs.put("aiCustomParameters", value(request.getAiCustomParameters()));
        inputs.put("maxTokens", Integer.toString(request.getMaxAllowedTokens()));
        inputs.put("useLocalMcp", Boolean.toString(request.getUseLocalMcp()));
        inputs.put("useMcpTools", Boolean.toString(request.getUseMcpTools()));
        inputs.put("ragEnabled", Boolean.toString(request.getRagEnabled()));
        inputs.put("projectRules", value(request.getProjectRules()));
        inputs.put("vcsProvider", value(request.getVcsProvider()));
        return digest(inputs);
    }

    private static String digest(Map<String, String> inputs) {
        StringBuilder canonical = new StringBuilder();
        for (Map.Entry<String, String> entry : inputs.entrySet()) {
            String current = value(entry.getValue());
            canonical.append(entry.getKey().length()).append(':').append(entry.getKey())
                    .append('=').append(current.length()).append(':').append(current).append('\n');
        }
        return sha256(canonical.toString());
    }

    private static String value(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(digest.length * 2);
            for (byte current : digest) {
                result.append(String.format("%02x", current & 0xff));
            }
            return result.toString();
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }
}
