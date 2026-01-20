package org.rostilos.codecrow.core.dto.analysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("ProjectTrends")
class ProjectTrendsTest {

    @Test
    @DisplayName("should set and get issuesResolvedLast7Days")
    void shouldSetAndGetIssuesResolvedLast7Days() {
        ProjectTrends trends = new ProjectTrends();
        trends.setIssuesResolvedLast7Days(25);
        
        assertThat(trends.getIssuesResolvedLast7Days()).isEqualTo(25);
    }

    @Test
    @DisplayName("should set and get newIssuesLast7Days")
    void shouldSetAndGetNewIssuesLast7Days() {
        ProjectTrends trends = new ProjectTrends();
        trends.setNewIssuesLast7Days(10);
        
        assertThat(trends.getNewIssuesLast7Days()).isEqualTo(10);
    }

    @Test
    @DisplayName("should set and get averageResolutionTime")
    void shouldSetAndGetAverageResolutionTime() {
        ProjectTrends trends = new ProjectTrends();
        trends.setAverageResolutionTime("2d 5h");
        
        assertThat(trends.getAverageResolutionTime()).isEqualTo("2d 5h");
    }

    @Test
    @DisplayName("should handle null averageResolutionTime")
    void shouldHandleNullAverageResolutionTime() {
        ProjectTrends trends = new ProjectTrends();
        
        assertThat(trends.getAverageResolutionTime()).isNull();
    }

    @Test
    @DisplayName("should default to zero for numeric values")
    void shouldDefaultToZeroForNumericValues() {
        ProjectTrends trends = new ProjectTrends();
        
        assertThat(trends.getIssuesResolvedLast7Days()).isEqualTo(0);
        assertThat(trends.getNewIssuesLast7Days()).isEqualTo(0);
    }
}
