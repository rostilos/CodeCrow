package org.rostilos.codecrow.vcsclient.config;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class OkHttpConfigTest {

    @Test
    void testOkHttpClientBuilder_CreatesBuilder() {
        OkHttpConfig config = new OkHttpConfig();

        OkHttpClient.Builder builder = config.okHttpClientBuilder();

        assertThat(builder).isNotNull();
    }

    @Test
    void testOkHttpClientBuilder_CanBuildClient() {
        OkHttpConfig config = new OkHttpConfig();

        OkHttpClient.Builder builder = config.okHttpClientBuilder();
        OkHttpClient client = builder.build();

        assertThat(client).isNotNull();
    }

    @Test
    void testOkHttpClientBuilder_CreatesNewInstance() {
        OkHttpConfig config = new OkHttpConfig();

        OkHttpClient.Builder builder1 = config.okHttpClientBuilder();
        OkHttpClient.Builder builder2 = config.okHttpClientBuilder();

        assertThat(builder1).isNotSameAs(builder2);
    }
}
