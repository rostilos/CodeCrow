package org.rostilos.codecrow.vcsclient.utils;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@DisplayName("LinksGenerator")
class LinksGeneratorTest {

    private static final String BASE_URL = "https://codecrow.example.com";

    private Project project;
    private Workspace workspace;

    @BeforeEach
    void setUp() {
        workspace = mock(Workspace.class);
        when(workspace.getSlug()).thenReturn("my-workspace");

        project = mock(Project.class);
        when(project.getWorkspace()).thenReturn(workspace);
        when(project.getNamespace()).thenReturn("my-project");
        when(project.getId()).thenReturn(1L);
    }

    @Nested
    @DisplayName("createDashboardUrl()")
    class CreateDashboardUrlTests {

        @Test
        @DisplayName("should generate correct dashboard URL")
        void shouldGenerateCorrectDashboardUrl() {
            String url = LinksGenerator.createDashboardUrl(BASE_URL, project);

            assertThat(url).isEqualTo("https://codecrow.example.com/dashboard/my-workspace/projects/my-project");
        }

        @Test
        @DisplayName("should use 'default' workspace when workspace is null")
        void shouldUseDefaultWhenWorkspaceIsNull() {
            when(project.getWorkspace()).thenReturn(null);

            String url = LinksGenerator.createDashboardUrl(BASE_URL, project);

            assertThat(url).isEqualTo("https://codecrow.example.com/dashboard/default/projects/my-project");
        }

        @Test
        @DisplayName("should use 'default' workspace when slug is null")
        void shouldUseDefaultWhenSlugIsNull() {
            when(workspace.getSlug()).thenReturn(null);

            String url = LinksGenerator.createDashboardUrl(BASE_URL, project);

            assertThat(url).isEqualTo("https://codecrow.example.com/dashboard/default/projects/my-project");
        }
    }

    @Nested
    @DisplayName("createIssueUrl()")
    class CreateIssueUrlTests {

        @Test
        @DisplayName("should generate correct issue URL")
        void shouldGenerateCorrectIssueUrl() {
            String url = LinksGenerator.createIssueUrl(BASE_URL, project, 123L);

            assertThat(url).isEqualTo("https://codecrow.example.com/dashboard/my-workspace/projects/my-project/issues/123");
        }

        @Test
        @DisplayName("should handle different issue IDs")
        void shouldHandleDifferentIssueIds() {
            String url = LinksGenerator.createIssueUrl(BASE_URL, project, 999999L);

            assertThat(url).contains("/issues/999999");
        }
    }

    @Nested
    @DisplayName("createBranchIssuesUrl()")
    class CreateBranchIssuesUrlTests {

        @Test
        @DisplayName("should generate correct branch issues URL")
        void shouldGenerateCorrectBranchIssuesUrl() {
            String url = LinksGenerator.createBranchIssuesUrl(BASE_URL, project, "main");

            assertThat(url).isEqualTo("https://codecrow.example.com/dashboard/my-workspace/projects/my-project/branches/main/issues");
        }

        @Test
        @DisplayName("should encode special characters in branch name")
        void shouldEncodeSpecialCharactersInBranchName() {
            String url = LinksGenerator.createBranchIssuesUrl(BASE_URL, project, "feature/my branch");

            assertThat(url).contains("feature%2Fmy%20branch");
        }
    }

    @Nested
    @DisplayName("createBranchIssuesUrlWithSeverity()")
    class CreateBranchIssuesUrlWithSeverityTests {

        @Test
        @DisplayName("should generate URL with severity filter for HIGH")
        void shouldGenerateUrlWithSeverityFilterHigh() {
            String url = LinksGenerator.createBranchIssuesUrlWithSeverity(BASE_URL, project, "main", IssueSeverity.HIGH);

            assertThat(url).contains("/issues?severity=HIGH");
        }

        @Test
        @DisplayName("should generate URL with severity filter for MEDIUM")
        void shouldGenerateUrlWithSeverityFilterMedium() {
            String url = LinksGenerator.createBranchIssuesUrlWithSeverity(BASE_URL, project, "develop", IssueSeverity.MEDIUM);

            assertThat(url).contains("branches/develop/issues?severity=MEDIUM");
        }

        @Test
        @DisplayName("should generate URL with severity filter for INFO")
        void shouldGenerateUrlWithSeverityFilterInfo() {
            String url = LinksGenerator.createBranchIssuesUrlWithSeverity(BASE_URL, project, "main", IssueSeverity.INFO);

            assertThat(url).contains("?severity=INFO");
        }
    }

    @Nested
    @DisplayName("createBranchIssuesUrlWithStatus()")
    class CreateBranchIssuesUrlWithStatusTests {

        @Test
        @DisplayName("should generate URL with status filter")
        void shouldGenerateUrlWithStatusFilter() {
            String url = LinksGenerator.createBranchIssuesUrlWithStatus(BASE_URL, project, "main", "OPEN");

            assertThat(url).contains("/issues?status=OPEN");
        }

        @Test
        @DisplayName("should handle different status values")
        void shouldHandleDifferentStatusValues() {
            String url = LinksGenerator.createBranchIssuesUrlWithStatus(BASE_URL, project, "main", "RESOLVED");

            assertThat(url).contains("?status=RESOLVED");
        }
    }

    @Nested
    @DisplayName("createStatusUrl()")
    class CreateStatusUrlTests {

        @Test
        @DisplayName("should generate correct status URL with PR ID and version")
        void shouldGenerateCorrectStatusUrl() {
            String url = LinksGenerator.createStatusUrl(BASE_URL, project, 42L, 3, "SUCCESS");

            assertThat(url).isEqualTo("https://codecrow.example.com/dashboard/my-workspace/projects/my-project?prId=42&version=3&status=SUCCESS");
        }

        @Test
        @DisplayName("should handle different status values")
        void shouldHandleDifferentStatusValues() {
            String url = LinksGenerator.createStatusUrl(BASE_URL, project, 1L, 1, "FAILED");

            assertThat(url).contains("&status=FAILED");
        }
    }

    @Nested
    @DisplayName("createSeverityUrl()")
    class CreateSeverityUrlTests {

        @Test
        @DisplayName("should generate correct severity URL")
        void shouldGenerateCorrectSeverityUrl() {
            String url = LinksGenerator.createSeverityUrl(BASE_URL, project, 50L, IssueSeverity.HIGH, 2);

            assertThat(url).isEqualTo("https://codecrow.example.com/dashboard/my-workspace/projects/my-project?prId=50&version=2&severity=HIGH");
        }

        @Test
        @DisplayName("should handle different severities")
        void shouldHandleDifferentSeverities() {
            String urlMedium = LinksGenerator.createSeverityUrl(BASE_URL, project, 1L, IssueSeverity.MEDIUM, 1);
            String urlInfo = LinksGenerator.createSeverityUrl(BASE_URL, project, 1L, IssueSeverity.INFO, 1);

            assertThat(urlMedium).contains("severity=MEDIUM");
            assertThat(urlInfo).contains("severity=INFO");
        }
    }

    @Nested
    @DisplayName("createPullRequestUrl()")
    class CreatePullRequestUrlTests {

        @Test
        @DisplayName("should generate PR URL when PR number is present")
        void shouldGeneratePrUrlWhenPrNumberPresent() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getPrNumber()).thenReturn(123L);

            String url = LinksGenerator.createPullRequestUrl(analysis);

            assertThat(url).contains("/pull-requests/123");
        }

        @Test
        @DisplayName("should return null when PR number is null")
        void shouldReturnNullWhenPrNumberIsNull() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getPrNumber()).thenReturn(null);

            String url = LinksGenerator.createPullRequestUrl(analysis);

            assertThat(url).isNull();
        }
    }

    @Nested
    @DisplayName("createPlatformAnalysisUrl()")
    class CreatePlatformAnalysisUrlTests {

        @Test
        @DisplayName("should generate platform analysis URL")
        void shouldGeneratePlatformAnalysisUrl() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getProject()).thenReturn(project);

            String url = LinksGenerator.createPlatformAnalysisUrl(BASE_URL, analysis, 99L);

            assertThat(url).isEqualTo("https://codecrow.example.com/dashboard/my-workspace/projects/my-project?prId=99");
        }

        @Test
        @DisplayName("should return null when project is null")
        void shouldReturnNullWhenProjectIsNull() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getProject()).thenReturn(null);

            String url = LinksGenerator.createPlatformAnalysisUrl(BASE_URL, analysis, 99L);

            assertThat(url).isNull();
        }

        @Test
        @DisplayName("should return null when platformPrEntityId is null")
        void shouldReturnNullWhenPlatformPrEntityIdIsNull() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getProject()).thenReturn(project);

            String url = LinksGenerator.createPlatformAnalysisUrl(BASE_URL, analysis, null);

            assertThat(url).isNull();
        }
    }

    @Nested
    @DisplayName("createMediaFileUrl()")
    class CreateMediaFileUrlTests {

        @Test
        @DisplayName("should generate media file URL")
        void shouldGenerateMediaFileUrl() {
            String url = LinksGenerator.createMediaFileUrl(BASE_URL, "images/logo.png");

            assertThat(url).isEqualTo("https://codecrow.example.com/images/logo.png");
        }

        @Test
        @DisplayName("should handle paths with special characters")
        void shouldHandlePathsWithSpecialCharacters() {
            String url = LinksGenerator.createMediaFileUrl(BASE_URL, "uploads/file%20name.pdf");

            assertThat(url).isEqualTo("https://codecrow.example.com/uploads/file%20name.pdf");
        }
    }
}
