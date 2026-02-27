package org.rostilos.codecrow.core.util.tracking;

import java.util.*;
import java.util.stream.Collectors;

/**
 * The result of running the {@link IssueTracker} — a bidirectional mapping between
 * raw (new) issues and base (existing) issues, plus the unmatched remainders on each side.
 * <p>
 * Uses {@link IdentityHashMap} internally so that tracking works correctly even when
 * multiple issues have the same natural key (e.g. same fingerprint before being assigned
 * a database ID). This mirrors SonarQube's approach for the same reason.
 *
 * @param <RAW>  the type of new/incoming issues (implements {@link Trackable})
 * @param <BASE> the type of existing/persisted issues (implements {@link Trackable})
 */
public final class Tracking<RAW extends Trackable, BASE extends Trackable> {

    /**
     * A matched pair of a raw issue and its corresponding base issue, along with
     * the confidence level indicating which tracker pass produced the match.
     */
    public record MatchedPair<RAW extends Trackable, BASE extends Trackable>(
            RAW raw,
            BASE base,
            TrackingConfidence confidence
    ) {}

    /** Raw → Base mapping (each raw matched to at most one base). */
    private final IdentityHashMap<RAW, BASE> rawToBase = new IdentityHashMap<>();

    /** Base → Raw mapping (each base matched to at most one raw). */
    private final IdentityHashMap<BASE, RAW> baseToRaw = new IdentityHashMap<>();

    /** Confidence level for each matched pair, keyed by raw issue. */
    private final IdentityHashMap<RAW, TrackingConfidence> confidenceMap = new IdentityHashMap<>();

    /** All raw issues participating in this tracking. */
    private final Set<RAW> allRaws;

    /** All base issues participating in this tracking. */
    private final Set<BASE> allBases;

    /**
     * @param rawIssues  all new/incoming issues
     * @param baseIssues all existing/persisted issues
     */
    public Tracking(Collection<RAW> rawIssues, Collection<BASE> baseIssues) {
        // Use identity-based sets to avoid hash collisions
        this.allRaws = Collections.newSetFromMap(new IdentityHashMap<>());
        this.allRaws.addAll(rawIssues);
        this.allBases = Collections.newSetFromMap(new IdentityHashMap<>());
        this.allBases.addAll(baseIssues);
    }

    /**
     * Record a match between a raw and base issue.
     *
     * @param raw        the new issue
     * @param base       the existing issue it matches
     * @param confidence which tracker pass produced this match
     * @throws IllegalStateException if either raw or base is already matched
     */
    public void match(RAW raw, BASE base, TrackingConfidence confidence) {
        if (rawToBase.containsKey(raw)) {
            throw new IllegalStateException("Raw issue already matched: " + raw);
        }
        if (baseToRaw.containsKey(base)) {
            throw new IllegalStateException("Base issue already matched: " + base);
        }
        rawToBase.put(raw, base);
        baseToRaw.put(base, raw);
        confidenceMap.put(raw, confidence);
    }

    /**
     * Check whether a raw issue has been matched.
     */
    public boolean isRawMatched(RAW raw) {
        return rawToBase.containsKey(raw);
    }

    /**
     * Check whether a base issue has been matched.
     */
    public boolean isBaseMatched(BASE base) {
        return baseToRaw.containsKey(base);
    }

    /**
     * Get the base issue that a raw was matched to.
     *
     * @return the matched base, or {@code null}
     */
    public BASE getBaseFor(RAW raw) {
        return rawToBase.get(raw);
    }

    /**
     * Get the raw issue that a base was matched to.
     *
     * @return the matched raw, or {@code null}
     */
    public RAW getRawFor(BASE base) {
        return baseToRaw.get(base);
    }

    /**
     * Get the confidence level for a matched raw issue.
     *
     * @return the confidence, or {@link TrackingConfidence#NONE} if not matched
     */
    public TrackingConfidence getConfidence(RAW raw) {
        return confidenceMap.getOrDefault(raw, TrackingConfidence.NONE);
    }

    /**
     * Get all matched pairs with their confidence levels.
     *
     * @return unmodifiable list of matched pairs
     */
    public List<MatchedPair<RAW, BASE>> getMatchedPairs() {
        return rawToBase.entrySet().stream()
                .map(e -> new MatchedPair<>(e.getKey(), e.getValue(), confidenceMap.get(e.getKey())))
                .collect(Collectors.toUnmodifiableList());
    }

    /**
     * Raw issues that were NOT matched to any base issue → genuinely new issues.
     */
    public List<RAW> getUnmatchedRaws() {
        return allRaws.stream()
                .filter(r -> !rawToBase.containsKey(r))
                .collect(Collectors.toUnmodifiableList());
    }

    /**
     * Base issues that were NOT matched to any raw issue → issues that no longer exist
     * (candidates for auto-resolution).
     */
    public List<BASE> getUnmatchedBases() {
        return allBases.stream()
                .filter(b -> !baseToRaw.containsKey(b))
                .collect(Collectors.toUnmodifiableList());
    }

    /**
     * @return true if all raw issues have been matched (short-circuit for tracker)
     */
    public boolean isComplete() {
        return rawToBase.size() == allRaws.size();
    }

    /**
     * @return the number of matched pairs
     */
    public int matchedCount() {
        return rawToBase.size();
    }

    /**
     * @return the total number of raw issues
     */
    public int rawCount() {
        return allRaws.size();
    }

    /**
     * @return the total number of base issues
     */
    public int baseCount() {
        return allBases.size();
    }
}
