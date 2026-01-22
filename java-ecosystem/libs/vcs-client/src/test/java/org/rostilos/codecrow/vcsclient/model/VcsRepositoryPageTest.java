package org.rostilos.codecrow.vcsclient.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("VcsRepositoryPage")
class VcsRepositoryPageTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        VcsRepository repo = VcsRepository.minimal("id", "slug", "ns");
        List<VcsRepository> items = List.of(repo);
        
        VcsRepositoryPage page = new VcsRepositoryPage(
                items,
                2,
                25,
                1,
                100,
                true,
                true
        );
        
        assertThat(page.items()).hasSize(1);
        assertThat(page.page()).isEqualTo(2);
        assertThat(page.pageSize()).isEqualTo(25);
        assertThat(page.itemCount()).isEqualTo(1);
        assertThat(page.totalCount()).isEqualTo(100);
        assertThat(page.hasNext()).isTrue();
        assertThat(page.hasPrevious()).isTrue();
    }

    @Test
    @DisplayName("should create first page")
    void shouldCreateFirstPage() {
        VcsRepositoryPage page = new VcsRepositoryPage(
                List.of(),
                1,
                10,
                0,
                50,
                true,
                false
        );
        
        assertThat(page.page()).isEqualTo(1);
        assertThat(page.hasNext()).isTrue();
        assertThat(page.hasPrevious()).isFalse();
    }

    @Test
    @DisplayName("should create last page")
    void shouldCreateLastPage() {
        VcsRepositoryPage page = new VcsRepositoryPage(
                List.of(),
                5,
                10,
                5,
                45,
                false,
                true
        );
        
        assertThat(page.hasNext()).isFalse();
        assertThat(page.hasPrevious()).isTrue();
    }

    @Nested
    @DisplayName("empty factory")
    class EmptyFactory {

        @Test
        @DisplayName("should create empty page")
        void shouldCreateEmptyPage() {
            VcsRepositoryPage page = VcsRepositoryPage.empty();
            
            assertThat(page.items()).isEmpty();
            assertThat(page.page()).isEqualTo(1);
            assertThat(page.pageSize()).isEqualTo(0);
            assertThat(page.itemCount()).isEqualTo(0);
            assertThat(page.totalCount()).isEqualTo(0);
            assertThat(page.hasNext()).isFalse();
            assertThat(page.hasPrevious()).isFalse();
        }
    }

    @Test
    @DisplayName("should allow null total count")
    void shouldAllowNullTotalCount() {
        VcsRepositoryPage page = new VcsRepositoryPage(
                List.of(),
                1,
                10,
                0,
                null,
                false,
                false
        );
        
        assertThat(page.totalCount()).isNull();
    }

    @Test
    @DisplayName("should be equal for same values")
    void shouldBeEqualForSameValues() {
        List<VcsRepository> items = List.of();
        VcsRepositoryPage page1 = new VcsRepositoryPage(items, 1, 10, 0, 50, true, false);
        VcsRepositoryPage page2 = new VcsRepositoryPage(items, 1, 10, 0, 50, true, false);
        
        assertThat(page1).isEqualTo(page2);
        assertThat(page1.hashCode()).isEqualTo(page2.hashCode());
    }
}
