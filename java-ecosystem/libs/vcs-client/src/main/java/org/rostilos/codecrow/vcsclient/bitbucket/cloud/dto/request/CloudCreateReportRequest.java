package org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.request;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.CodeInsightsReport;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.ReportData;

import java.util.Date;
import java.util.List;

public class CloudCreateReportRequest extends CodeInsightsReport {
    @JsonProperty("created_on")
    @JsonFormat(shape = JsonFormat.Shape.STRING, pattern = "yyyy-MM-dd'T'HH:mm:ssZ")
    private final Date createdDate;
    @JsonProperty("logo_url")
    private final String logoUrl;
    @JsonProperty("report_type")
    private final String reportType;
    @JsonProperty("remote_link_enabled")
    private final boolean remoteLinkEnabled;

    @JsonCreator
    public CloudCreateReportRequest(
            List<ReportData> data,
            String details,
            String title,
            String reporter,
            Date createdDate,
            String link,
            String logoUrl,
            String reportType,
            String result) {
        super(data, details, title, reporter, link, result);
        this.createdDate = createdDate;
        this.logoUrl = logoUrl;
        this.reportType = reportType;
        this.remoteLinkEnabled = true;
    }

    public Date getCreatedDate() {
        return createdDate;
    }

    public String getLogoUrl() {
        return logoUrl;
    }

    public String getReportType() {
        return reportType;
    }

    public Boolean getRemoteLinkEnabled() {
        return remoteLinkEnabled;
    }
}