package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketParticipant")
class BitbucketParticipantTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        BitbucketAccount user = new BitbucketAccount();
        user.username = "reviewer";
        user.displayName = "Code Reviewer";
        
        BitbucketParticipant participant = new BitbucketParticipant(
                "participant",
                user,
                "REVIEWER",
                true,
                "approved",
                "2024-01-15T10:30:00Z"
        );
        
        assertThat(participant.type()).isEqualTo("participant");
        assertThat(participant.user()).isNotNull();
        assertThat(participant.user().getUsername()).isEqualTo("reviewer");
        assertThat(participant.role()).isEqualTo("REVIEWER");
        assertThat(participant.approved()).isTrue();
        assertThat(participant.state()).isEqualTo("approved");
        assertThat(participant.participatedOn()).isEqualTo("2024-01-15T10:30:00Z");
    }

    @Test
    @DisplayName("should create with null values")
    void shouldCreateWithNullValues() {
        BitbucketParticipant participant = new BitbucketParticipant(null, null, null, false, null, null);
        
        assertThat(participant.type()).isNull();
        assertThat(participant.user()).isNull();
        assertThat(participant.role()).isNull();
        assertThat(participant.approved()).isFalse();
        assertThat(participant.state()).isNull();
        assertThat(participant.participatedOn()).isNull();
    }

    @Test
    @DisplayName("should be equal for same values")
    void shouldBeEqualForSameValues() {
        BitbucketParticipant p1 = new BitbucketParticipant("type", null, "REVIEWER", true, "approved", "2024-01-15");
        BitbucketParticipant p2 = new BitbucketParticipant("type", null, "REVIEWER", true, "approved", "2024-01-15");
        
        assertThat(p1).isEqualTo(p2);
        assertThat(p1.hashCode()).isEqualTo(p2.hashCode());
    }
}
