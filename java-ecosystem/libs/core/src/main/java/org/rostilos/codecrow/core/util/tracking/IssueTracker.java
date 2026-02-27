package org.rostilos.codecrow.core.util.tracking;

import java.util.*;
import java.util.function.Function;

/**
 * Deterministic 4-pass issue tracker that matches raw (new) issues to base (existing) issues
 * using content-based identity. This replaces the previous AI-only reconciliation approach
 * with a predictable, efficient, and auditable matching algorithm.
 * <p>
 * The 4 passes, in order of decreasing strictness:
 * <ol>
 *   <li><b>EXACT</b> — fingerprint + line number + line hash (exact same issue at exact same line)</li>
 *   <li><b>SHIFTED</b> — fingerprint + line hash (same issue, line moved but content identical)</li>
 *   <li><b>EDITED</b> — fingerprint + line number (same issue type/title at same line, code changed)</li>
 *   <li><b>WEAK</b> — fingerprint only (same issue type/title, both code and position changed)</li>
 * </ol>
 * <p>
 * Each pass uses a <i>search key</i> to group issues. Within each group, when multiple
 * candidates exist, the one closest to the base issue's line number is preferred.
 * Once an issue is matched in a pass, it is excluded from subsequent passes.
 * <p>
 * After all 4 passes, unmatched raws are genuinely new issues and unmatched bases are
 * candidates for resolution (or can be sent to AI for a final check).
 *
 * <h3>Design rationale</h3>
 * <ul>
 *   <li>SonarQube uses a 6-pass cascade; we use 4 because AI-generated issues have less
 *       structural predictability than static-analysis rules, making the two block-hash
 *       passes unnecessary.</li>
 *   <li>Each pass is purely deterministic and runs in O(n) time per pass.</li>
 *   <li>The tracker is stateless — create a new instance for each file being tracked.</li>
 * </ul>
 *
 * @param <RAW>  the type of new/incoming issues (implements {@link Trackable})
 * @param <BASE> the type of existing/persisted issues (implements {@link Trackable})
 */
public final class IssueTracker<RAW extends Trackable, BASE extends Trackable> {

    private IssueTracker() { /* use static track() method */ }

    /**
     * Run the full 4-pass tracking between raw and base issues.
     *
     * @param rawIssues  new/incoming issues (from the latest analysis)
     * @param baseIssues existing/persisted issues (from the branch state)
     * @param <RAW>      raw issue type
     * @param <BASE>     base issue type
     * @return the tracking result with all matches, unmatched raws, and unmatched bases
     */
    public static <RAW extends Trackable, BASE extends Trackable> Tracking<RAW, BASE> track(
            Collection<RAW> rawIssues, Collection<BASE> baseIssues) {

        Tracking<RAW, BASE> tracking = new Tracking<>(rawIssues, baseIssues);

        if (rawIssues.isEmpty() || baseIssues.isEmpty()) {
            return tracking;
        }

        // Pass 1: EXACT — fingerprint + line + lineHash
        matchPass(tracking, rawIssues, baseIssues, TrackingConfidence.EXACT,
                IssueTracker::exactKey);

        if (tracking.isComplete()) return tracking;

        // Pass 2: SHIFTED — fingerprint + lineHash (line moved, content same)
        matchPass(tracking, rawIssues, baseIssues, TrackingConfidence.SHIFTED,
                IssueTracker::shiftedKey);

        if (tracking.isComplete()) return tracking;

        // Pass 3: EDITED — fingerprint + line (content changed, same position)
        matchPass(tracking, rawIssues, baseIssues, TrackingConfidence.EDITED,
                IssueTracker::editedKey);

        if (tracking.isComplete()) return tracking;

        // Pass 4: WEAK — fingerprint only (both position and content changed)
        matchPass(tracking, rawIssues, baseIssues, TrackingConfidence.WEAK,
                IssueTracker::weakKey);

        return tracking;
    }

    /**
     * Run a single matching pass: group both raw and base issues by the given key function,
     * then within each group, match pairs using closest-line-number preference.
     */
    private static <RAW extends Trackable, BASE extends Trackable> void matchPass(
            Tracking<RAW, BASE> tracking,
            Collection<RAW> allRaws,
            Collection<BASE> allBases,
            TrackingConfidence confidence,
            Function<Trackable, String> keyFn) {

        // Group unmatched raws by key
        Map<String, List<RAW>> rawsByKey = new LinkedHashMap<>();
        for (RAW raw : allRaws) {
            if (!tracking.isRawMatched(raw)) {
                String key = keyFn.apply(raw);
                if (key != null) {
                    rawsByKey.computeIfAbsent(key, k -> new ArrayList<>()).add(raw);
                }
            }
        }

        if (rawsByKey.isEmpty()) return;

        // Group unmatched bases by key
        Map<String, List<BASE>> basesByKey = new LinkedHashMap<>();
        for (BASE base : allBases) {
            if (!tracking.isBaseMatched(base)) {
                String key = keyFn.apply(base);
                if (key != null) {
                    basesByKey.computeIfAbsent(key, k -> new ArrayList<>()).add(base);
                }
            }
        }

        if (basesByKey.isEmpty()) return;

        // Match within each key group
        for (Map.Entry<String, List<RAW>> entry : rawsByKey.entrySet()) {
            String key = entry.getKey();
            List<BASE> baseCandidates = basesByKey.get(key);
            if (baseCandidates == null || baseCandidates.isEmpty()) {
                continue;
            }

            List<RAW> rawCandidates = entry.getValue();
            matchCandidates(tracking, rawCandidates, baseCandidates, confidence);
        }
    }

    /**
     * Within a group of issues sharing the same key, match raw→base pairs using
     * closest-line-number greedy algorithm.
     * <p>
     * For each base issue (in order), find the unmatched raw issue with the closest
     * line number and record the match. This greedy approach works well because issues
     * within the same key group are already strongly similar.
     */
    private static <RAW extends Trackable, BASE extends Trackable> void matchCandidates(
            Tracking<RAW, BASE> tracking,
            List<RAW> rawCandidates,
            List<BASE> baseCandidates,
            TrackingConfidence confidence) {

        // Track which base candidates have been consumed in this round
        Set<BASE> consumed = Collections.newSetFromMap(new IdentityHashMap<>());

        for (RAW raw : rawCandidates) {
            if (tracking.isRawMatched(raw)) {
                continue;
            }

            BASE bestBase = null;
            int bestDistance = Integer.MAX_VALUE;

            for (BASE base : baseCandidates) {
                if (consumed.contains(base) || tracking.isBaseMatched(base)) {
                    continue;
                }
                int distance = lineDistance(raw, base);
                if (distance < bestDistance) {
                    bestDistance = distance;
                    bestBase = base;
                }
            }

            if (bestBase != null) {
                tracking.match(raw, bestBase, confidence);
                consumed.add(bestBase);
            }
        }
    }

    /**
     * Compute the absolute distance between two issue line numbers.
     * Issues without line numbers are treated as infinitely far apart but still matchable.
     */
    private static int lineDistance(Trackable a, Trackable b) {
        Integer lineA = a.getLine();
        Integer lineB = b.getLine();
        if (lineA == null || lineB == null) {
            return Integer.MAX_VALUE - 1; // matchable but least preferred
        }
        return Math.abs(lineA - lineB);
    }

    // ========================================================================
    //  Key functions for each pass
    // ========================================================================

    /**
     * Pass 1 key: fingerprint + line + lineHash (EXACT match)
     */
    private static String exactKey(Trackable issue) {
        String fp = issue.getIssueFingerprint();
        Integer line = issue.getLine();
        String hash = issue.getLineHash();
        if (fp == null || line == null || hash == null) {
            return null;
        }
        return fp + ":" + line + ":" + hash;
    }

    /**
     * Pass 2 key: fingerprint + lineHash (SHIFTED — same content, different line)
     */
    private static String shiftedKey(Trackable issue) {
        String fp = issue.getIssueFingerprint();
        String hash = issue.getLineHash();
        if (fp == null || hash == null) {
            return null;
        }
        return fp + ":" + hash;
    }

    /**
     * Pass 3 key: fingerprint + line (EDITED — same position, content changed)
     */
    private static String editedKey(Trackable issue) {
        String fp = issue.getIssueFingerprint();
        Integer line = issue.getLine();
        if (fp == null || line == null) {
            return null;
        }
        return fp + ":" + line;
    }

    /**
     * Pass 4 key: fingerprint only (WEAK — same issue type, everything else changed)
     */
    private static String weakKey(Trackable issue) {
        return issue.getIssueFingerprint();
    }
}
