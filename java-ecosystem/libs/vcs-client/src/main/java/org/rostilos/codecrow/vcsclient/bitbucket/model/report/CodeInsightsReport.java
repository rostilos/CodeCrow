package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
public class CodeInsightsReport {
    @JsonProperty("data")
    private final List<ReportData> data;
    @JsonProperty("details")
    private final String details;
    @JsonProperty("title")
    private final String title;
    @JsonProperty("reporter")
    private final String reporter;
    @JsonProperty("link")
    private final String link;
    @JsonProperty("result")
    private final String result;

    public CodeInsightsReport(List<ReportData> data, String details, String title, String reporter, String link, String result) {
        this.data = data;
        this.details = details;
        this.title = title;
        this.reporter = reporter;
        this.link = link;
        this.result = result;
    }

    public List<ReportData> getData() {
        return data;
    }

    public String getDetails() {
        return details;
    }

    public String getTitle() {
        return title;
    }

    public String getReporter() {
        return reporter;
    }

    public String getLink() {
        return link;
    }

    public String getResult() {
        return result;
    }
}
