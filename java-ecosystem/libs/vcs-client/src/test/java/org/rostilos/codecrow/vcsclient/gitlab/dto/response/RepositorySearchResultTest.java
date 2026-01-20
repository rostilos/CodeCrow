package org.rostilos.codecrow.vcsclient.gitlab.dto.response;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Collections;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GitLab RepositorySearchResult")
class RepositorySearchResultTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        List<Map<String, Object>> items = List.of(
                Map.of("name", "project1", "path_with_namespace", "group/project1"),
                Map.of("name", "project2", "path_with_namespace", "group/project2")
        );
        
        RepositorySearchResult result = new RepositorySearchResult(items, true, 200);
        
        assertThat(result.items()).hasSize(2);
        assertThat(result.hasNext()).isTrue();
        assertThat(result.totalCount()).isEqualTo(200);
    }

    @Test
    @DisplayName("should handle empty items")
    void shouldHandleEmptyItems() {
        RepositorySearchResult result = new RepositorySearchResult(Collections.emptyList(), false, 0);
        
        assertThat(result.items()).isEmpty();
        assertThat(result.hasNext()).isFalse();
        assertThat(result.totalCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("should handle null items")
    void shouldHandleNullItems() {
        RepositorySearchResult result = new RepositorySearchResult(null, false, null);
        
        assertThat(result.items()).isNull();
        assertThat(result.totalCount()).isNull();
    }

    @Test
    @DisplayName("should indicate pagination status")
    void shouldIndicatePaginationStatus() {
        RepositorySearchResult hasMore = new RepositorySearchResult(List.of(Map.of("id", 1)), true, 50);
        RepositorySearchResult noMore = new RepositorySearchResult(List.of(Map.of("id", 1)), false, 1);
        
        assertThat(hasMore.hasNext()).isTrue();
        assertThat(noMore.hasNext()).isFalse();
    }
}
