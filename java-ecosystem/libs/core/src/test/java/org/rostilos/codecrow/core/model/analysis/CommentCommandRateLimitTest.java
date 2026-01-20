package org.rostilos.codecrow.core.model.analysis;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class CommentCommandRateLimitTest {

    @Test
    void shouldCreateCommentCommandRateLimit() {
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit();
        assertThat(rateLimit).isNotNull();
    }

    @Test
    void shouldCreateWithProjectAndWindowStart() {
        Project project = new Project();
        OffsetDateTime windowStart = OffsetDateTime.now();
        
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit(project, windowStart);
        
        assertThat(rateLimit.getProject()).isEqualTo(project);
        assertThat(rateLimit.getWindowStart()).isEqualTo(windowStart);
        assertThat(rateLimit.getCommandCount()).isEqualTo(0);
    }

    @Test
    void shouldGetAndSetId() {
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit();
        // ID is auto-generated, verify it's null for new entity
        assertThat(rateLimit.getId()).isNull();
    }

    @Test
    void shouldSetAndGetProject() {
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit();
        Project project = new Project();
        
        rateLimit.setProject(project);
        
        assertThat(rateLimit.getProject()).isEqualTo(project);
    }

    @Test
    void shouldSetAndGetWindowStart() {
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit();
        OffsetDateTime windowStart = OffsetDateTime.now();
        
        rateLimit.setWindowStart(windowStart);
        
        assertThat(rateLimit.getWindowStart()).isEqualTo(windowStart);
    }

    @Test
    void shouldSetAndGetCommandCount() {
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit();
        rateLimit.setCommandCount(5);
        
        assertThat(rateLimit.getCommandCount()).isEqualTo(5);
    }

    @Test
    void shouldIncrementCommandCount() {
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit();
        assertThat(rateLimit.getCommandCount()).isEqualTo(0);
        
        rateLimit.incrementCommandCount();
        
        assertThat(rateLimit.getCommandCount()).isEqualTo(1);
        assertThat(rateLimit.getLastCommandAt()).isNotNull();
    }

    @Test
    void shouldIncrementCommandCountMultipleTimes() {
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit();
        
        rateLimit.incrementCommandCount();
        rateLimit.incrementCommandCount();
        rateLimit.incrementCommandCount();
        
        assertThat(rateLimit.getCommandCount()).isEqualTo(3);
    }

    @Test
    void shouldUpdateLastCommandAtOnIncrement() {
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit();
        assertThat(rateLimit.getLastCommandAt()).isNull();
        
        OffsetDateTime before = OffsetDateTime.now();
        rateLimit.incrementCommandCount();
        OffsetDateTime after = OffsetDateTime.now();
        
        assertThat(rateLimit.getLastCommandAt()).isNotNull();
        assertThat(rateLimit.getLastCommandAt()).isAfterOrEqualTo(before);
        assertThat(rateLimit.getLastCommandAt()).isBeforeOrEqualTo(after);
    }

    @Test
    void shouldSetAndGetLastCommandAt() {
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit();
        OffsetDateTime timestamp = OffsetDateTime.now();
        
        rateLimit.setLastCommandAt(timestamp);
        
        assertThat(rateLimit.getLastCommandAt()).isEqualTo(timestamp);
    }

    @Test
    void shouldTrackRateLimitWindow() {
        Project project = new Project();
        OffsetDateTime windowStart = OffsetDateTime.now().minusMinutes(5);
        
        CommentCommandRateLimit rateLimit = new CommentCommandRateLimit(project, windowStart);
        rateLimit.incrementCommandCount();
        rateLimit.incrementCommandCount();
        
        assertThat(rateLimit.getProject()).isEqualTo(project);
        assertThat(rateLimit.getWindowStart()).isEqualTo(windowStart);
        assertThat(rateLimit.getCommandCount()).isEqualTo(2);
        assertThat(rateLimit.getLastCommandAt()).isNotNull();
    }
}
