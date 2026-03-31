package org.rostilos.codecrow.analysisengine.util;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;

/**
 * Shared utility for computing delta diffs between commits with content filtering.
 * <p>
 * Centralises the fetch + filter + error-handling logic that was previously
 * duplicated across BitbucketAiClientService, GithubAiClientService, and
 * GitlabAiClientService. The caller passes a provider-agnostic
 * {@link CommitRangeDiffFetcher} lambda so no VCS-specific imports are needed.
 */
public final class VcsDiffUtils {

    private static final Logger log = LoggerFactory.getLogger(VcsDiffUtils.class);

    /**
     * When the delta diff size exceeds this fraction of the full diff size,
     * the analysis escalates from INCREMENTAL back to FULL mode because the
     * delta is almost as large as the original.
     */
    public static final double INCREMENTAL_ESCALATION_THRESHOLD = 0.5;

    /**
     * Minimum delta-diff size (in characters) below which the diff is considered
     * trivially small and always qualifies for incremental mode.
     */
    public static final int MIN_DELTA_DIFF_SIZE = 500;

    private VcsDiffUtils() {
        // utility class
    }

    /**
     * Provider-agnostic callback for obtaining the raw diff between two commits.
     * <p>
     * Implementations typically delegate to a VCS-specific action class
     * (e.g.&nbsp;{@code GetCommitRangeDiffAction}) or to
     * {@code VcsOperationsService.getCommitRangeDiff}.
     */
    @FunctionalInterface
    public interface CommitRangeDiffFetcher {
        /**
         * @param workspace    workspace slug / owner / namespace
         * @param repoSlug     repository slug
         * @param baseCommit   base (previously analysed) commit hash
         * @param headCommit   head (current) commit hash
         * @return raw unified diff between the two commits
         * @throws IOException on network or parsing errors
         */
        String fetch(String workspace, String repoSlug,
                     String baseCommit, String headCommit) throws IOException;
    }

    /**
     * Fetches the delta diff between two commits, applies the content filter,
     * and returns the filtered result. Returns {@code null} on failure
     * (non-blocking — errors are logged as warnings).
     *
     * @param fetcher        provider-agnostic diff retriever
     * @param workspace      workspace slug / owner / namespace
     * @param repoSlug       repository slug
     * @param baseCommit     base commit hash (the last successfully analysed one)
     * @param headCommit     head commit hash (the current one)
     * @param contentFilter  content-size filter to strip oversised file diffs
     * @return filtered delta diff, or {@code null} if fetching failed
     */
    public static String fetchDeltaDiff(
            CommitRangeDiffFetcher fetcher,
            String workspace,
            String repoSlug,
            String baseCommit,
            String headCommit,
            DiffContentFilter contentFilter) {
        try {
            String rawDeltaDiff = fetcher.fetch(workspace, repoSlug, baseCommit, headCommit);
            return contentFilter.filterDiff(rawDeltaDiff);
        } catch (IOException e) {
            log.warn("Failed to fetch delta diff from {} to {}: {}",
                    truncateHash(baseCommit),
                    truncateHash(headCommit),
                    e.getMessage());
            return null;
        }
    }

    /**
     * Determines whether an incremental analysis should be escalated back to
     * FULL mode based on the delta-diff size relative to the full diff.
     *
     * @param deltaDiffLength  length of the delta diff in characters
     * @param fullDiffLength   length of the full PR/commit diff in characters
     * @return {@code true} if the delta is large enough to warrant full re-analysis
     */
    public static boolean shouldEscalateToFull(int deltaDiffLength, int fullDiffLength) {
        if (deltaDiffLength <= MIN_DELTA_DIFF_SIZE) {
            return false;
        }
        if (fullDiffLength <= 0) {
            return false;
        }
        return (double) deltaDiffLength / fullDiffLength > INCREMENTAL_ESCALATION_THRESHOLD;
    }

    private static String truncateHash(String hash) {
        return (hash != null && hash.length() > 7)
                ? hash.substring(0, 7)
                : String.valueOf(hash);
    }
}
