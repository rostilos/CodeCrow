package org.rostilos.codecrow.pipelineagent.qadoc;

import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

/** The persisted intent also recovers reviews whose completion event was lost. */
@Component
public class QaAutoDocListener {
    private static final Logger log=LoggerFactory.getLogger(QaAutoDocListener.class);
    public static final String COMMENT_MARKER = "<!-- codecrow-qa-autodoc -->";
    public static final String COMMENT_MARKER_PREFIX = "<!-- codecrow-qa-autodoc";
    private final QaDocQueueService queue;
    public QaAutoDocListener(QaDocQueueService queue) { this.queue=queue; }

    @Async @EventListener
    public void onAnalysisCompleted(AnalysisCompletedEvent event) {
        if (!event.isSuccessful() || event.getProjectId() == null || event.getPrNumber() == null) return;
        if (event.getMetrics() == null || !(event.getMetrics().get("analysisId") instanceof Number id)) return;
        try { queue.enqueue(event.getProjectId(),id.longValue()); }
        catch (Exception e) { log.warn("QA enqueue interrupted for analysis {}; persisted intent will be retried",id,e); }
    }

    public static boolean isPublicPreviewOnlyComment(String body) {
        if (body == null || (!body.contains("/share#token=ccs_")
                && !body.contains("/share?token=ccs_"))) {
            return false;
        }
        String withoutPreviewLink = body
                .replaceAll("https?://\\S+/share(?:#|\\?)token=ccs_[A-Za-z0-9_-]+", "")
                .replace("View QA test cases in CodeCrow", "")
                .replaceAll("[\\[\\]()\\s]", "");
        return withoutPreviewLink.isBlank();
    }

}
