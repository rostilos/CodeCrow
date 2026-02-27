package org.rostilos.codecrow.vcsclient.model;

import java.time.OffsetDateTime;
import java.util.List;

public record VcsCommit(
        String hash,
        String message,
        String authorName,
        String authorEmail,
        OffsetDateTime timestamp,
        List<String> parentHashes
) {}
