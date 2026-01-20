package org.rostilos.codecrow.vcsclient.bitbucket.cloud.dto.response;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions.SearchBitbucketCloudReposAction;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class RepositorySearchResultTest {

    @Test
    void testGetTotalPages_WithTotalSize_ReturnsCalculatedPages() {
        RepositorySearchResult result = new RepositorySearchResult(
                List.of(),
                1,
                10,
                10,
                25,
                true,
                false
        );

        Integer totalPages = result.getTotalPages();

        assertThat(totalPages).isEqualTo(3);
    }

    @Test
    void testGetTotalPages_WithNullTotalSize_ReturnsNull() {
        RepositorySearchResult result = new RepositorySearchResult(
                List.of(),
                1,
                10,
                10,
                null,
                false,
                false
        );

        Integer totalPages = result.getTotalPages();

        assertThat(totalPages).isNull();
    }

    @Test
    void testGetTotalPages_WithZeroPageSize_ReturnsNull() {
        RepositorySearchResult result = new RepositorySearchResult(
                List.of(),
                1,
                0,
                0,
                25,
                false,
                false
        );

        Integer totalPages = result.getTotalPages();

        assertThat(totalPages).isNull();
    }

    @Test
    void testGetTotalPages_ExactMatch_ReturnsCorrectPages() {
        RepositorySearchResult result = new RepositorySearchResult(
                List.of(),
                1,
                10,
                10,
                30,
                false,
                false
        );

        Integer totalPages = result.getTotalPages();

        assertThat(totalPages).isEqualTo(3);
    }

    @Test
    void testToString_FormatsCorrectly() {
        List<SearchBitbucketCloudReposAction.Repository> repos = List.of();
        RepositorySearchResult result = new RepositorySearchResult(
                repos,
                2,
                10,
                10,
                25,
                true,
                true
        );

        String toString = result.toString();

        assertThat(toString).contains("repositories=0");
        assertThat(toString).contains("currentPage=2");
        assertThat(toString).contains("pageSize=10");
        assertThat(toString).contains("totalSize=25");
        assertThat(toString).contains("hasNext=true");
        assertThat(toString).contains("hasPrevious=true");
    }

    @Test
    void testConstructor_AllFieldsAccessible() {
        List<SearchBitbucketCloudReposAction.Repository> repos = List.of();
        RepositorySearchResult result = new RepositorySearchResult(
                repos,
                1,
                20,
                15,
                100,
                true,
                false
        );

        assertThat(result.repositories()).isEqualTo(repos);
        assertThat(result.currentPage()).isEqualTo(1);
        assertThat(result.pageSize()).isEqualTo(20);
        assertThat(result.currentPageSize()).isEqualTo(15);
        assertThat(result.totalSize()).isEqualTo(100);
        assertThat(result.hasNext()).isTrue();
        assertThat(result.hasPrevious()).isFalse();
    }
}
