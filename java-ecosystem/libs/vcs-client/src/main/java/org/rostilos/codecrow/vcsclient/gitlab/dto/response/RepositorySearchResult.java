package org.rostilos.codecrow.vcsclient.gitlab.dto.response;

import java.util.List;
import java.util.Map;

public record RepositorySearchResult(
        List<Map<String, Object>> items,
        boolean hasNext,
        Integer totalCount
) {}
