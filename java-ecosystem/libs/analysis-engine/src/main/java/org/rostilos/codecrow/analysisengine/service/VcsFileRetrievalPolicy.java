package org.rostilos.codecrow.analysisengine.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Locale;

/**
 * Shared policy for bounded VCS file retrieval.
 *
 * <p>All analysis consumers must use the same threshold so a branch operation
 * cannot exhaust the provider quota before a later RAG or reconciliation stage
 * gets a chance to run.</p>
 */
@Component
public class VcsFileRetrievalPolicy {

    private final int archiveFileThreshold;

    public VcsFileRetrievalPolicy(
            @Value("${codecrow.vcs.file-retrieval.archive-threshold:"
                    + "${codecrow.rag.incremental.archive-file-threshold:25}}")
            int archiveFileThreshold) {
        this.archiveFileThreshold = Math.max(0, archiveFileThreshold);
    }

    public boolean shouldUseArchive(int requestedFileCount) {
        return requestedFileCount > archiveFileThreshold;
    }

    public boolean allowPerFileFallback(int requestedFileCount) {
        return !shouldUseArchive(requestedFileCount);
    }

    public int archiveFileThreshold() {
        return archiveFileThreshold;
    }

    public boolean isRateLimited(Throwable failure) {
        Throwable current = failure;
        while (current != null) {
            String message = current.getMessage();
            if (message != null) {
                String normalized = message.toLowerCase(Locale.ROOT);
                if (normalized.contains("429")
                        || normalized.contains("rate limit")
                        || normalized.contains("rate-limit")
                        || normalized.contains("too many requests")) {
                    return true;
                }
            }
            current = current.getCause();
        }
        return false;
    }
}
