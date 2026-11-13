package org.rostilos.codecrow.vcsclient;

import org.slf4j.MDC;
import java.util.Map;

/** Restores the caller's logging context when a worker or nested acquisition finishes. */
public final class VcsRequestContext implements AutoCloseable {
    private final Map<String, String> previous = MDC.getCopyOfContextMap();

    public VcsRequestContext(Long projectId, String jobId, String stage) {
        if (projectId != null) MDC.put("projectId", projectId.toString());
        if (jobId != null) MDC.put("jobId", jobId);
        if (stage != null) MDC.put("vcsStage", stage);
    }

    @Override
    public void close() {
        if (previous == null) MDC.clear();
        else MDC.setContextMap(previous);
    }
}
