package org.rostilos.codecrow.webserver.analysis.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.springframework.data.domain.PageRequest;
import java.util.List;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class DeveloperContextServiceTest {
    @Test
    void usesScopedExactCommitThenWarnsWhenOnlyStalePartialFindingsExist() {
        var repository = mock(CodeAnalysisRepository.class);
        var service = new DeveloperContextService(repository);
        var analysis = new CodeAnalysis(); analysis.setCommitHash("previous-head"); analysis.setStatus(AnalysisStatus.PARTIAL);
        when(repository.findDeveloperContext(10L, null, null, "feature", PageRequest.of(0, 1))).thenReturn(List.of(analysis));
        var result = service.get(10L, "feature", "current-head", null);
        assertThat(result.get("available")).isEqualTo(true);
        assertThat(result.get("matchesLocalCommit")).isEqualTo(false);
        assertThat(result.get("analysisStatus")).isEqualTo(AnalysisStatus.PARTIAL);
        verify(repository).findDeveloperContext(10L, null, "current-head", "feature", PageRequest.of(0, 1));
        assertThat(service.get(11L, "feature", "current-head", null).get("available")).isEqualTo(false);
        assertThat(service.get(10L, null, null, null).get("available")).isEqualTo(false);
    }
}
