package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonProperty;

public class CloudAnnotation extends CodeInsightsAnnotation {
    private final String externalId;
    private final String link;
    private final String annotationType;

    @JsonCreator
    public CloudAnnotation(String externalId,
                           int line,
                           String link,
                           String message,
                           String path,
                           String severity,
                           String annotationType) {
        super(line, message, path, severity);
        this.externalId = externalId;
        this.link = link;
        this.annotationType = annotationType;
    }

    @Override
    @JsonProperty("summary")
    public String getMessage() {
        return super.getMessage();
    }

    @JsonProperty("external_id")
    public String getExternalId() {
        return externalId;
    }

    @JsonProperty("link")
    public String getLink() {
        return link;
    }

    @JsonProperty("annotation_type")
    public String getAnnotationType() {
        return annotationType;
    }
}
