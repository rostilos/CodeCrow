package org.rostilos.codecrow.vcsclient.offline.p003;

import okhttp3.Interceptor;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Protocol;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.rostilos.codecrow.testsupport.offline.ExternalCall;
import org.rostilos.codecrow.testsupport.offline.ExternalCallLedger;
import org.rostilos.codecrow.testsupport.offline.OfflineNetworkExtension;
import org.rostilos.codecrow.testsupport.offline.ScriptedScenario;
import org.rostilos.codecrow.testsupport.offline.ScriptedStep;
import org.rostilos.codecrow.testsupport.offline.UnexpectedExternalCall;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudClient;
import org.rostilos.codecrow.vcsclient.github.GitHubClient;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabClient;

import java.io.IOException;
import java.util.List;
import java.util.function.Function;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class VcsConnectionAdapterContractTest {

    @RegisterExtension
    final OfflineNetworkExtension offlineNetwork = new OfflineNetworkExtension();

    @Test
    void productionAdaptersAndScriptedFakeShareTheConnectionContract() throws Exception {
        for (AdapterCase adapter : adapters()) {
            assertContract(adapter, 200, true);
            assertContract(adapter, 401, false);
        }

        assertThat(offlineNetwork.ledger().entries()).hasSize(12);
        assertThat(offlineNetwork.ledger().simulatedCallCount()).isEqualTo(12);
        assertThat(offlineNetwork.ledger().liveCallCount()).isZero();
    }

    @Test
    void realProductionAdapterCannotEscapeWhenItsTransportIsNotFaked() {
        VcsClient production = new GitHubClient(new OkHttpClient());

        assertThatThrownBy(production::validateConnection)
                .isInstanceOf(UnexpectedExternalCall.class);
        ExternalCall blocked = offlineNetwork.ledger().entries().get(0);
        assertThat(offlineNetwork.ledger().entries()).containsExactly(blocked);
        assertThat(blocked).satisfies(call -> {
            assertThat(call.boundary()).isEqualTo("network");
            assertThat(call.live()).isFalse();
            assertThat(call.operation()).isEqualTo("resolve");
            assertThat(call.outcome()).isEqualTo("blocked");
            assertThat(call.phase()).isEqualTo("PRE_DNS");
            assertThat(call.simulated()).isFalse();
            assertThat(call.target()).isEqualTo("api.github.com:0");
        });
        offlineNetwork.acknowledgeBlockedCall(
                blocked, "network", "resolve", "PRE_DNS", "api.github.com:0"
        );
    }

    private void assertContract(AdapterCase adapter, int status, boolean expected) throws Exception {
        ExternalCallLedger ledger = offlineNetwork.ledger();
        int firstEntry = ledger.entries().size();
        ScriptedScenario script = new ScriptedScenario(
                "vcs-connection-" + adapter.name(),
                "vcs_" + adapter.name(),
                "fake://" + adapter.name(),
                List.of(
                        ScriptedStep.response("production_validate", 1, Integer.toString(status)),
                        ScriptedStep.response("fake_validate", 1, Integer.toString(status))
                ),
                ledger
        );
        OkHttpClient transportFake = new OkHttpClient.Builder()
                .addInterceptor(new ScriptedStatusInterceptor(script))
                .build();

        ConnectionProbe productionAdapter = adapter.factory().apply(transportFake)::validateConnection;
        ConnectionProbe narrowFake = () ->
                script.next("fake_validate").step().payload().asInt() < 300;

        assertThat(productionAdapter.validate()).isEqualTo(expected);
        assertThat(narrowFake.validate()).isEqualTo(expected);
        assertThat(script.remaining()).isZero();
        assertThat(ledger.entries().subList(firstEntry, firstEntry + 2)).satisfiesExactly(
                call -> assertSimulatedVcsCall(
                        call, adapter, "production_validate", firstEntry + 1L
                ),
                call -> assertSimulatedVcsCall(
                        call, adapter, "fake_validate", firstEntry + 2L
                )
        );
    }

    private static void assertSimulatedVcsCall(
            ExternalCall call,
            AdapterCase adapter,
            String operation,
            long sequence
    ) {
        assertThat(call.boundary()).isEqualTo("vcs_" + adapter.name());
        assertThat(call.live()).isFalse();
        assertThat(call.operation()).isEqualTo(operation);
        assertThat(call.outcome()).isEqualTo("response");
        assertThat(call.phase()).isEqualTo("SIMULATED");
        assertThat(call.sequence()).isEqualTo(sequence);
        assertThat(call.simulated()).isTrue();
        assertThat(call.target()).isEqualTo("fake://" + adapter.name());
    }

    private static List<AdapterCase> adapters() {
        return Stream.of(
                new AdapterCase("github", GitHubClient::new),
                new AdapterCase(
                        "gitlab",
                        client -> new GitLabClient(client, "https://gitlab.fixture.invalid/api/v4")
                ),
                new AdapterCase("bitbucket", BitbucketCloudClient::new)
        ).toList();
    }

    private record AdapterCase(String name, Function<OkHttpClient, VcsClient> factory) {
    }

    @FunctionalInterface
    private interface ConnectionProbe {
        boolean validate() throws IOException;
    }

    private record ScriptedStatusInterceptor(ScriptedScenario script) implements Interceptor {
        private static final MediaType JSON = MediaType.get("application/json");

        @Override
        public Response intercept(Chain chain) {
            Request request = chain.request();
            int status = script.next("production_validate").step().payload().asInt();
            return new Response.Builder()
                    .request(request)
                    .protocol(Protocol.HTTP_1_1)
                    .code(status)
                    .message("scripted")
                    .body(ResponseBody.create("{}", JSON))
                    .build();
        }
    }
}
