package org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.request;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.DataValue;
import org.rostilos.codecrow.vcsclient.bitbucket.model.report.ReportData;

import java.util.Collections;
import java.util.Date;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("CloudCreateReportRequest")
class CloudCreateReportRequestTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        Date now = new Date();
        List<ReportData> data = Collections.emptyList();
        
        CloudCreateReportRequest request = new CloudCreateReportRequest(
                data,
                "Analysis details",
                "Code Analysis Report",
                "CodeCrow",
                now,
                "https://codecrow.io/analysis/123",
                "https://codecrow.io/logo.png",
                "COVERAGE",
                "PASSED"
        );
        
        assertThat(request.getData()).isEqualTo(data);
        assertThat(request.getDetails()).isEqualTo("Analysis details");
        assertThat(request.getTitle()).isEqualTo("Code Analysis Report");
        assertThat(request.getReporter()).isEqualTo("CodeCrow");
        assertThat(request.getCreatedDate()).isEqualTo(now);
        assertThat(request.getLink()).isEqualTo("https://codecrow.io/analysis/123");
        assertThat(request.getLogoUrl()).isEqualTo("https://codecrow.io/logo.png");
        assertThat(request.getReportType()).isEqualTo("COVERAGE");
        assertThat(request.getResult()).isEqualTo("PASSED");
        assertThat(request.getRemoteLinkEnabled()).isTrue();
    }

    @Test
    @DisplayName("should handle null optional fields")
    void shouldHandleNullOptionalFields() {
        CloudCreateReportRequest request = new CloudCreateReportRequest(
                null,
                null,
                "Title",
                "Reporter",
                null,
                null,
                null,
                null,
                null
        );
        
        assertThat(request.getData()).isNull();
        assertThat(request.getDetails()).isNull();
        assertThat(request.getCreatedDate()).isNull();
        assertThat(request.getLogoUrl()).isNull();
        assertThat(request.getReportType()).isNull();
    }

    @Test
    @DisplayName("should inherit from CodeInsightsReport")
    void shouldInheritFromCodeInsightsReport() {
        DataValue.Text textValue = new DataValue.Text("test-value");
        ReportData reportData = new ReportData("title", textValue);
        List<ReportData> data = List.of(reportData);
        
        CloudCreateReportRequest request = new CloudCreateReportRequest(
                data,
                "Details",
                "Title",
                "Reporter",
                new Date(),
                "https://link.com",
                "https://logo.png",
                "SECURITY",
                "FAILED"
        );
        
        assertThat(request.getData()).hasSize(1);
        assertThat(request.getData().get(0).getTitle()).isEqualTo("title");
    }

    @Test
    @DisplayName("remoteLinkEnabled should always be true")
    void remoteLinkEnabledShouldAlwaysBeTrue() {
        CloudCreateReportRequest request = new CloudCreateReportRequest(
                null, null, "Title", "Reporter", null, null, null, null, null
        );
        
        assertThat(request.getRemoteLinkEnabled()).isTrue();
    }
}
