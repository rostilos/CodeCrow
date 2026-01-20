package org.rostilos.codecrow.vcsclient.bitbucket.model.report;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("CodeInsightsReport")
class CodeInsightsReportTest {

    @Nested
    @DisplayName("Constructor")
    class ConstructorTests {

        @Test
        @DisplayName("should create report with all fields")
        void shouldCreateReportWithAllFields() {
            ReportData data1 = new ReportData("Issue", new DataValue.Text("5"));
            List<ReportData> data = List.of(data1);
            
            CodeInsightsReport report = new CodeInsightsReport(
                    data,
                    "Analysis details",
                    "CodeCrow Report",
                    "CodeCrow",
                    "https://codecrow.example.com",
                    "PASSED"
            );
            
            assertThat(report.getData()).hasSize(1);
            assertThat(report.getDetails()).isEqualTo("Analysis details");
            assertThat(report.getTitle()).isEqualTo("CodeCrow Report");
            assertThat(report.getReporter()).isEqualTo("CodeCrow");
            assertThat(report.getLink()).isEqualTo("https://codecrow.example.com");
            assertThat(report.getResult()).isEqualTo("PASSED");
        }

        @Test
        @DisplayName("should create report with null fields")
        void shouldCreateReportWithNullFields() {
            CodeInsightsReport report = new CodeInsightsReport(
                    null,
                    null,
                    "Title",
                    "Reporter",
                    null,
                    null
            );
            
            assertThat(report.getData()).isNull();
            assertThat(report.getDetails()).isNull();
            assertThat(report.getTitle()).isEqualTo("Title");
            assertThat(report.getReporter()).isEqualTo("Reporter");
            assertThat(report.getLink()).isNull();
            assertThat(report.getResult()).isNull();
        }

        @Test
        @DisplayName("should create report with empty data list")
        void shouldCreateReportWithEmptyDataList() {
            CodeInsightsReport report = new CodeInsightsReport(
                    List.of(),
                    "Details",
                    "Title",
                    "Reporter",
                    "https://link.com",
                    "FAILED"
            );
            
            assertThat(report.getData()).isEmpty();
        }
    }

    @Nested
    @DisplayName("Getters")
    class GetterTests {

        @Test
        @DisplayName("getData() should return the data list")
        void getDataShouldReturnDataList() {
            ReportData data = new ReportData("Test", new DataValue.Text("10"));
            List<ReportData> dataList = List.of(data);
            
            CodeInsightsReport report = new CodeInsightsReport(
                    dataList, null, null, null, null, null);
            
            assertThat(report.getData()).isSameAs(dataList);
        }

        @Test
        @DisplayName("getResult() should return result status")
        void getResultShouldReturnResultStatus() {
            CodeInsightsReport passedReport = new CodeInsightsReport(
                    null, null, null, null, null, "PASSED");
            CodeInsightsReport failedReport = new CodeInsightsReport(
                    null, null, null, null, null, "FAILED");
            
            assertThat(passedReport.getResult()).isEqualTo("PASSED");
            assertThat(failedReport.getResult()).isEqualTo("FAILED");
        }
    }
}
