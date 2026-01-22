package org.rostilos.codecrow.vcsclient.github.dto.response;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Collections;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("RepositorySearchResult")
class RepositorySearchResultTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        List<Map<String, Object>> items = List.of(
                Map.of("name", "repo1", "full_name", "org/repo1"),
                Map.of("name", "repo2", "full_name", "org/repo2")
        );
        
        RepositorySearchResult result = new RepositorySearchResult(items, true, 100);
        
        assertThat(result.items()).hasSize(2);
        assertThat(result.hasNext()).isTrue();
        assertThat(result.totalCount()).isEqualTo(100);
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
    @DisplayName("should indicate more results available")
    void shouldIndicateMoreResultsAvailable() {
        RepositorySearchResult resultWithMore = new RepositorySearchResult(List.of(Map.of("name", "repo")), true, 50);
        RepositorySearchResult resultNoMore = new RepositorySearchResult(List.of(Map.of("name", "repo")), false, 1);
        
        assertThat(resultWithMore.hasNext()).isTrue();
        assertThat(resultNoMore.hasNext()).isFalse();
    }
}
