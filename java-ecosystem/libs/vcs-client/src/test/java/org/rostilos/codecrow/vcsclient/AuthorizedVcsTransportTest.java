package org.rostilos.codecrow.vcsclient;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class AuthorizedVcsTransportTest {

    @Test
    void stringRepresentationNeverContainsAccessToken() {
        AuthorizedVcsTransport transport = AuthorizedVcsTransport.withAccessToken(
                new OkHttpClient(), "highly-sensitive-token");

        assertThat(transport.toString())
                .contains("accessToken=redacted")
                .doesNotContain("highly-sensitive-token");
        assertThat(transport.accessToken()).contains("highly-sensitive-token");
    }
}
