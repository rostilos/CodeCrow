package org.rostilos.codecrow.core.model.codeanalysis;

import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.JsonToken;

import java.io.IOException;
import java.util.regex.Pattern;

final class ReviewReportComment {
    private static final JsonFactory JSON = new JsonFactory();
    private static final Pattern LEGACY_ENVELOPE = Pattern.compile(
            "\\A(\\*\\*Review (?:incomplete|completed with reduced coverage)\\.\\*\\*[^\\r\\n]*\\R\\R)?"
                    + "(?:\\x60{3}(?:json)?\\s*)?\\{\\s*\"report\"\\s*:\\s*");
    private static final Pattern VERIFICATION_NOTICE = Pattern.compile(
            "\\R\\RIndependent evidence verification remains unresolved for (\\d+) of (\\d+) "
                    + "(?:findings|active review candidates before server ingestion)\\."
                    + "(?:\\R\\R\\*\\*Saved open findings: \\d+\\.\\*\\* Source-anchor checks, "
                    + "deduplication, and issue status updates can change the displayed count\\.)?\\s*\\z");
    private static final String UNAVAILABLE = "**Review summary unavailable.** "
            + "The generated report was incomplete. See the saved findings for this review.";

    private ReviewReportComment() {}

    static String forDisplay(String comment, int savedOpenFindings) {
        if (comment == null) return null;
        var notice = VERIFICATION_NOTICE.matcher(comment);
        boolean hasNotice = notice.find();
        String report = hasNotice ? comment.substring(0, notice.start()) : comment;
        var envelope = LEGACY_ENVELOPE.matcher(report);
        if (envelope.find()) {
            String prefix = envelope.group(1);
            report = (prefix == null ? "" : prefix) + decodeMarkdown(report.substring(envelope.end()));
        }
        if (!hasNotice) return report;

        String candidates = notice.group(2);
        String result = report + "\n\nIndependent evidence verification remains unresolved for "
                + notice.group(1) + " of " + candidates + " active review candidates before server ingestion.";
        if (!Integer.toString(savedOpenFindings).equals(candidates)) {
            result += "\n\n**Saved open findings: " + savedOpenFindings + ".** Source-anchor checks, "
                    + "deduplication, and issue status updates can change the displayed count.";
        }
        return result;
    }

    private static String decodeMarkdown(String value) {
        try (var parser = JSON.createParser(value)) {
            if (parser.nextToken() == JsonToken.VALUE_STRING) {
                String markdown = parser.getText();
                if (!markdown.isBlank()) return markdown;
            }
        } catch (IOException ignored) {
            // A truncated report string has no complete Markdown value to display.
        }
        return UNAVAILABLE;
    }
}
