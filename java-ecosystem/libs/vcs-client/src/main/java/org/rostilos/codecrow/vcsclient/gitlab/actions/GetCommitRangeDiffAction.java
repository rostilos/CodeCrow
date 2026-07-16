package org.rostilos.codecrow.vcsclient.gitlab.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.rostilos.codecrow.vcsclient.diff.DiffAcquisitionException;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventoryParser;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * Action to compare two commits in GitLab and get the diff.
 */
public class GetCommitRangeDiffAction {

    private static final Logger log = LoggerFactory.getLogger(GetCommitRangeDiffAction.class);
    private final OkHttpClient authorizedOkHttpClient;

    public GetCommitRangeDiffAction(OkHttpClient authorizedOkHttpClient) {
        this.authorizedOkHttpClient = authorizedOkHttpClient;
    }

    /**
     * Get the diff between two commits.
     * 
     * @param namespace the project namespace (group or user)
     * @param project the project path
     * @param baseCommitSha the base commit SHA
     * @param headCommitSha the head commit SHA
     * @return the diff as a unified diff string
     */
    public String getCommitRangeDiff(String namespace, String project, String baseCommitSha, String headCommitSha) throws IOException {
        String projectPath = namespace + "/" + project;
        String encodedPath = URLEncoder.encode(projectPath, StandardCharsets.UTF_8);
        
        // GitLab uses compare endpoint for commit range diff
        String apiUrl = String.format("%s/projects/%s/repository/compare?from=%s&to=%s",
                GitLabConfig.API_BASE, encodedPath, 
                URLEncoder.encode(baseCommitSha, StandardCharsets.UTF_8),
                URLEncoder.encode(headCommitSha, StandardCharsets.UTF_8));

        Request req = new Request.Builder()
                .url(apiUrl)
                .header("Accept", "application/json")
                .get()
                .build();

        try (Response resp = authorizedOkHttpClient.newCall(req).execute()) {
            if (!resp.isSuccessful()) {
                String body = resp.body() != null ? resp.body().string() : "";
                String msg = String.format("GitLab returned non-success response %d for URL %s: %s",
                        resp.code(), apiUrl, body);
                log.warn(msg);
                throw new IOException(msg);
            }
            
            ResponseBody body = resp.body();
            if (body == null) {
                throw acquisitionFailure(
                        ExactDiffInventory.GapType.PATCH_UNAVAILABLE,
                        "GitLab compare response body is missing");
            }
            String responseBody = body.string();
            if (responseBody == null || responseBody.isBlank()) {
                throw acquisitionFailure(
                        ExactDiffInventory.GapType.MALFORMED,
                        "GitLab compare response body is empty or blank");
            }
            return buildUnifiedDiff(responseBody);
        }
    }

    /**
     * Build a unified diff from GitLab's compare response.
     */
    private String buildUnifiedDiff(String responseBody) throws IOException {
        StringBuilder combinedDiff = new StringBuilder();
        com.fasterxml.jackson.databind.ObjectMapper objectMapper = new com.fasterxml.jackson.databind.ObjectMapper();
        com.fasterxml.jackson.databind.JsonNode root;
        try {
            root = objectMapper.readTree(responseBody);
        } catch (com.fasterxml.jackson.core.JsonProcessingException exception) {
            throw acquisitionFailure(
                    ExactDiffInventory.GapType.MALFORMED,
                    "GitLab compare response is not valid JSON",
                    exception);
        }
        if (root == null || !root.isObject()) {
            throw acquisitionFailure(
                    ExactDiffInventory.GapType.MALFORMED,
                    "GitLab compare response must be a JSON object");
        }
        com.fasterxml.jackson.databind.JsonNode diffs = root.get("diffs");
        
        if (diffs == null || !diffs.isArray()) {
            throw acquisitionFailure(
                    ExactDiffInventory.GapType.MALFORMED,
                    "GitLab compare response is missing the typed diffs inventory");
        }
        
        for (com.fasterxml.jackson.databind.JsonNode diffEntry : diffs) {
            if (diffEntry == null || !diffEntry.isObject()) {
                throw acquisitionFailure(
                        ExactDiffInventory.GapType.MALFORMED,
                        "GitLab typed diff entry must be a JSON object");
            }
            if (optionalBoolean(diffEntry, "too_large")
                    || optionalBoolean(diffEntry, "collapsed")) {
                throw acquisitionFailure(
                        ExactDiffInventory.GapType.PROVIDER_TRUNCATED,
                        "GitLab compare response contains a truncated diff entry");
            }

            String oldPath = requiredPath(diffEntry, "old_path");
            String newPath = requiredPath(diffEntry, "new_path");
            String diff = requiredText(diffEntry, "diff");
            String oldMode = optionalText(diffEntry, "a_mode");
            String newMode = optionalText(diffEntry, "b_mode");
            boolean newFile = optionalBoolean(diffEntry, "new_file");
            boolean deletedFile = optionalBoolean(diffEntry, "deleted_file");
            boolean renamedFile = optionalBoolean(diffEntry, "renamed_file");
            int structuralFlags = (newFile ? 1 : 0)
                    + (deletedFile ? 1 : 0)
                    + (renamedFile ? 1 : 0);
            if (structuralFlags > 1) {
                throw acquisitionFailure(
                        ExactDiffInventory.GapType.MALFORMED,
                        "GitLab typed diff entry has conflicting change flags");
            }
            boolean modeChanged = oldMode != null
                    && newMode != null
                    && !oldMode.equals(newMode);
            if (diff.isBlank() && !diff.isEmpty()) {
                throw acquisitionFailure(
                        ExactDiffInventory.GapType.MALFORMED,
                        "GitLab typed diff entry contains a blank patch");
            }
            if (diff.isEmpty() && structuralFlags == 0 && !modeChanged) {
                throw acquisitionFailure(
                        ExactDiffInventory.GapType.PATCH_UNAVAILABLE,
                        "GitLab typed diff entry has no patch or structural metadata");
            }
            
            // Build unified diff header
            combinedDiff.append("diff --git ")
                    .append(quotedPath("a/", oldPath))
                    .append(" ")
                    .append(quotedPath("b/", newPath))
                    .append("\n");
            
            if (newFile) {
                appendNewFileMode(combinedDiff, newMode);
                combinedDiff.append("--- /dev/null\n");
                combinedDiff.append("+++ ").append(quotedPath("b/", newPath)).append("\n");
            } else if (deletedFile) {
                appendDeletedFileMode(combinedDiff, oldMode);
                combinedDiff.append("--- ").append(quotedPath("a/", oldPath)).append("\n");
                combinedDiff.append("+++ /dev/null\n");
            } else if (renamedFile) {
                appendChangedModes(combinedDiff, oldMode, newMode);
                combinedDiff.append("rename from ").append(quotedPath("", oldPath)).append("\n");
                combinedDiff.append("rename to ").append(quotedPath("", newPath)).append("\n");
                combinedDiff.append("--- ").append(quotedPath("a/", oldPath)).append("\n");
                combinedDiff.append("+++ ").append(quotedPath("b/", newPath)).append("\n");
            } else {
                appendChangedModes(combinedDiff, oldMode, newMode);
                combinedDiff.append("--- ").append(quotedPath("a/", oldPath)).append("\n");
                combinedDiff.append("+++ ").append(quotedPath("b/", newPath)).append("\n");
            }
            
            // Append the actual diff content
            if (!diff.isEmpty()) {
                combinedDiff.append(diff);
                if (!diff.endsWith("\n")) {
                    combinedDiff.append("\n");
                }
            }
            
            combinedDiff.append("\n");
        }

        String rawDiff = combinedDiff.toString();
        requireCompleteInventory(rawDiff);
        return rawDiff;
    }

    private static String requiredPath(
            com.fasterxml.jackson.databind.JsonNode entry,
            String fieldName
    ) throws DiffAcquisitionException {
        String value = requiredText(entry, fieldName);
        if (value.isBlank()
                || value.equals("/dev/null")
                || value.indexOf('\0') >= 0
                || value.indexOf('\n') >= 0
                || value.indexOf('\r') >= 0) {
            throw acquisitionFailure(
                    ExactDiffInventory.GapType.MALFORMED,
                    "GitLab typed diff entry has an invalid " + fieldName);
        }
        return value;
    }

    private static String requiredText(
            com.fasterxml.jackson.databind.JsonNode entry,
            String fieldName
    ) throws DiffAcquisitionException {
        com.fasterxml.jackson.databind.JsonNode value = entry.get(fieldName);
        if (value == null || !value.isTextual()) {
            throw acquisitionFailure(
                    ExactDiffInventory.GapType.MALFORMED,
                    "GitLab typed diff entry requires textual " + fieldName);
        }
        return value.textValue();
    }

    private static String optionalText(
            com.fasterxml.jackson.databind.JsonNode entry,
            String fieldName
    ) throws DiffAcquisitionException {
        com.fasterxml.jackson.databind.JsonNode value = entry.get(fieldName);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual() || value.textValue().isBlank()) {
            throw acquisitionFailure(
                    ExactDiffInventory.GapType.MALFORMED,
                    "GitLab typed diff entry has invalid " + fieldName);
        }
        return value.textValue();
    }

    private static boolean optionalBoolean(
            com.fasterxml.jackson.databind.JsonNode entry,
            String fieldName
    ) throws DiffAcquisitionException {
        com.fasterxml.jackson.databind.JsonNode value = entry.get(fieldName);
        if (value == null || value.isNull()) {
            return false;
        }
        if (!value.isBoolean()) {
            throw acquisitionFailure(
                    ExactDiffInventory.GapType.MALFORMED,
                    "GitLab typed diff entry has non-boolean " + fieldName);
        }
        return value.booleanValue();
    }

    private static void appendNewFileMode(StringBuilder target, String newMode) {
        if (newMode != null && !newMode.equals("0")) {
            target.append("new file mode ").append(newMode).append("\n");
        }
    }

    private static void appendDeletedFileMode(StringBuilder target, String oldMode) {
        if (oldMode != null && !oldMode.equals("0")) {
            target.append("deleted file mode ").append(oldMode).append("\n");
        }
    }

    private static void appendChangedModes(
            StringBuilder target,
            String oldMode,
            String newMode
    ) {
        if (oldMode != null && newMode != null && !oldMode.equals(newMode)) {
            target.append("old mode ").append(oldMode).append("\n");
            target.append("new mode ").append(newMode).append("\n");
        }
    }

    private static String quotedPath(String prefix, String path) {
        String candidate = prefix + path;
        if (candidate.matches("[A-Za-z0-9._/+@=-]+")) {
            return candidate;
        }
        return "\"" + candidate
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\t", "\\t") + "\"";
    }

    private static void requireCompleteInventory(String rawDiff)
            throws DiffAcquisitionException {
        ExactDiffInventory inventory = new ExactDiffInventoryParser().parse(rawDiff);
        if (inventory.completeness() == ExactDiffInventory.Completeness.COMPLETE) {
            return;
        }
        ExactDiffInventory.GapType reason = inventory.gaps().isEmpty()
                ? ExactDiffInventory.GapType.MALFORMED
                : inventory.gaps().get(0).type();
        throw acquisitionFailure(
                reason,
                "GitLab compare response did not produce a complete unified diff");
    }

    private static DiffAcquisitionException acquisitionFailure(
            ExactDiffInventory.GapType reason,
            String message
    ) {
        return new DiffAcquisitionException(reason, message);
    }

    private static DiffAcquisitionException acquisitionFailure(
            ExactDiffInventory.GapType reason,
            String message,
            Throwable cause
    ) {
        DiffAcquisitionException failure = acquisitionFailure(reason, message);
        failure.initCause(cause);
        return failure;
    }
}
