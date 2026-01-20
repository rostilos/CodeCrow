package org.rostilos.codecrow.core.model.vcs.config.cloud;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class BitbucketCloudConfigTest {

    @Test
    void testFullConstructor() {
        BitbucketCloudConfig config = new BitbucketCloudConfig("oauth-key", "oauth-token", "workspace-id");
        
        assertThat(config.oAuthKey()).isEqualTo("oauth-key");
        assertThat(config.oAuthToken()).isEqualTo("oauth-token");
        assertThat(config.workspaceId()).isEqualTo("workspace-id");
    }

    @Test
    void testRecordEquality() {
        BitbucketCloudConfig config1 = new BitbucketCloudConfig("key", "token", "workspace");
        BitbucketCloudConfig config2 = new BitbucketCloudConfig("key", "token", "workspace");
        
        assertThat(config1).isEqualTo(config2);
        assertThat(config1.hashCode()).isEqualTo(config2.hashCode());
    }

    @Test
    void testRecordInequality() {
        BitbucketCloudConfig config1 = new BitbucketCloudConfig("key1", "token", "workspace");
        BitbucketCloudConfig config2 = new BitbucketCloudConfig("key2", "token", "workspace");
        
        assertThat(config1).isNotEqualTo(config2);
    }

    @Test
    void testWithNullValues() {
        BitbucketCloudConfig config = new BitbucketCloudConfig(null, null, null);
        
        assertThat(config.oAuthKey()).isNull();
        assertThat(config.oAuthToken()).isNull();
        assertThat(config.workspaceId()).isNull();
    }

    @Test
    void testToString() {
        BitbucketCloudConfig config = new BitbucketCloudConfig("key", "token", "workspace");
        String str = config.toString();
        
        assertThat(str).contains("BitbucketCloudConfig");
        assertThat(str).contains("key");
        assertThat(str).contains("token");
        assertThat(str).contains("workspace");
    }

    @Test
    void testDifferentOAuthKeys() {
        BitbucketCloudConfig config1 = new BitbucketCloudConfig("key-123", "token", "workspace");
        BitbucketCloudConfig config2 = new BitbucketCloudConfig("key-456", "token", "workspace");
        
        assertThat(config1.oAuthKey()).isNotEqualTo(config2.oAuthKey());
        assertThat(config1).isNotEqualTo(config2);
    }

    @Test
    void testDifferentWorkspaceIds() {
        BitbucketCloudConfig config1 = new BitbucketCloudConfig("key", "token", "workspace1");
        BitbucketCloudConfig config2 = new BitbucketCloudConfig("key", "token", "workspace2");
        
        assertThat(config1.workspaceId()).isNotEqualTo(config2.workspaceId());
        assertThat(config1).isNotEqualTo(config2);
    }

    @Test
    void testDifferentOAuthTokens() {
        BitbucketCloudConfig config1 = new BitbucketCloudConfig("key", "token1", "workspace");
        BitbucketCloudConfig config2 = new BitbucketCloudConfig("key", "token2", "workspace");
        
        assertThat(config1.oAuthToken()).isNotEqualTo(config2.oAuthToken());
        assertThat(config1).isNotEqualTo(config2);
    }
}
