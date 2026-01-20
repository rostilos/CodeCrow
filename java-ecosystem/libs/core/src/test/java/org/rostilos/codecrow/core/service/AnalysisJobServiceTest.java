package org.rostilos.codecrow.core.service;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobLogLevel;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@DisplayName("AnalysisJobService")
class AnalysisJobServiceTest {

    @Nested
    @DisplayName("default methods")
    class DefaultMethodsTests {

        @Test
        @DisplayName("info() should call logToJob with INFO level")
        void infoShouldCallLogToJobWithInfoLevel() {
            TestAnalysisJobService service = spy(new TestAnalysisJobService());
            Job job = new Job();
            
            service.info(job, "test-state", "test message");
            
            verify(service).logToJob(job, JobLogLevel.INFO, "test-state", "test message");
        }

        @Test
        @DisplayName("warn() should call logToJob with WARN level")
        void warnShouldCallLogToJobWithWarnLevel() {
            TestAnalysisJobService service = spy(new TestAnalysisJobService());
            Job job = new Job();
            
            service.warn(job, "test-state", "warning message");
            
            verify(service).logToJob(job, JobLogLevel.WARN, "test-state", "warning message");
        }

        @Test
        @DisplayName("error() should call logToJob with ERROR level")
        void errorShouldCallLogToJobWithErrorLevel() {
            TestAnalysisJobService service = spy(new TestAnalysisJobService());
            Job job = new Job();
            
            service.error(job, "test-state", "error message");
            
            verify(service).logToJob(job, JobLogLevel.ERROR, "test-state", "error message");
        }


    }

    // Test implementation of the interface for testing default methods
    private static class TestAnalysisJobService implements AnalysisJobService {
        
        @Override
        public Job createRagIndexJob(org.rostilos.codecrow.core.model.project.Project project, 
                                      org.rostilos.codecrow.core.model.user.User triggeredBy) {
            return new Job();
        }

        @Override
        public Job createRagIndexJob(org.rostilos.codecrow.core.model.project.Project project, 
                                      boolean isInitial, 
                                      org.rostilos.codecrow.core.model.job.JobTriggerSource triggerSource) {
            return new Job();
        }

        @Override
        public void startJob(Job job) {}

        @Override
        public void logToJob(Job job, JobLogLevel level, String state, String message) {}

        @Override
        public void logToJob(Job job, JobLogLevel level, String state, String message, 
                            Map<String, Object> metadata) {}

        @Override
        public void completeJob(Job job, Map<String, Object> result) {}

        @Override
        public void failJob(Job job, String errorMessage) {}
    }
}
