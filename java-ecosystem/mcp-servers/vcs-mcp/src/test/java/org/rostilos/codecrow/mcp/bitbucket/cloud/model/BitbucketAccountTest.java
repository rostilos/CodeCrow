package org.rostilos.codecrow.mcp.bitbucket.cloud.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("BitbucketAccount")
class BitbucketAccountTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        BitbucketAccount account = new BitbucketAccount();
        account.uuid = "{12345}";
        account.username = "test-user";
        account.displayName = "Test User";
        account.accountId = "557058:abc";
        account.nickname = "tester";
        account.type = "user";
        account.links = Map.of("avatar", new BitbucketLink("https://avatar.url", "avatar"));
        
        assertThat(account.getUuid()).isEqualTo("{12345}");
        assertThat(account.getUsername()).isEqualTo("test-user");
        assertThat(account.getDisplayName()).isEqualTo("Test User");
        assertThat(account.getAccountId()).isEqualTo("557058:abc");
        assertThat(account.getNickname()).isEqualTo("tester");
        assertThat(account.getType()).isEqualTo("user");
        assertThat(account.getLinks()).containsKey("avatar");
    }

    @Test
    @DisplayName("should allow null values")
    void shouldAllowNullValues() {
        BitbucketAccount account = new BitbucketAccount();
        
        assertThat(account.getUuid()).isNull();
        assertThat(account.getUsername()).isNull();
        assertThat(account.getDisplayName()).isNull();
        assertThat(account.getAccountId()).isNull();
        assertThat(account.getNickname()).isNull();
        assertThat(account.getType()).isNull();
        assertThat(account.getLinks()).isNull();
    }
}
