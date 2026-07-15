package org.rostilos.codecrow.email.offline.p003;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.RegisterExtension;
import org.rostilos.codecrow.email.config.EmailProperties;
import org.rostilos.codecrow.email.service.EmailServiceImpl;
import org.rostilos.codecrow.testsupport.offline.ExternalCall;
import org.rostilos.codecrow.testsupport.offline.ExternalCallLedger;
import org.rostilos.codecrow.testsupport.offline.NetworkDenyGuard;
import org.rostilos.codecrow.testsupport.offline.OfflineNetworkExtension;
import org.rostilos.codecrow.testsupport.offline.ScriptedScenario;
import org.rostilos.codecrow.testsupport.offline.ScriptedStep;
import org.rostilos.codecrow.testsupport.offline.UnexpectedExternalCall;
import org.springframework.mail.javamail.JavaMailSenderImpl;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class EmailSmtpAdapterContractTest {

    @RegisterExtension
    final OfflineNetworkExtension offlineNetwork = new OfflineNetworkExtension();

    @Test
    void productionEmailServiceAndScriptedFakeShareTheDeliveryContract() throws Exception {
        ExpectedMail expected = new ExpectedMail(
                "recipient@fixture.invalid",
                "Offline delivery contract",
                "deterministic body"
        );
        ExternalCallLedger ledger = offlineNetwork.ledger();
        ScriptedScenario script = new ScriptedScenario(
                "email-simple-delivery-v1",
                "email",
                "fake-smtp:2525",
                List.of(
                        ScriptedStep.response("production_send", 1, "accepted"),
                        ScriptedStep.response("fake_send", 1, "accepted")
                ),
                ledger
        );
        try (LiteralSmtpFixture smtp = new LiteralSmtpFixture()) {
            try (NetworkDenyGuard.NetworkLease ignored = offlineNetwork.allowLoopback(
                    "127.0.0.1", smtp.port()
            )) {
                EmailServiceImpl production = new EmailServiceImpl(
                        mailSender("127.0.0.1", smtp.port()),
                        null,
                        enabledEmailProperties()
                );
                assertDeliveryContract(
                        mail -> {
                            assertThat(script.next("production_send").step().payload().asText())
                                    .isEqualTo("accepted");
                            production.sendSimpleEmail(mail.to(), mail.subject(), mail.body());
                        },
                        () -> smtp.awaitMessage().delivery(),
                        expected
                );
                assertThat(smtp.awaitMessage().rawMessage())
                        .contains("To: " + expected.to(), "Subject: " + expected.subject(), expected.body());
            }
        }

        assertThat(script.remaining()).isOne();
        assertThat(ledger.entries()).singleElement().satisfies(
                call -> assertSimulatedEmailCall(call, "production_send", 1)
        );
        AtomicReference<DeliveredMail> fakeDelivery = new AtomicReference<>();
        assertDeliveryContract(
                mail -> {
                    assertThat(script.next("fake_send").step().payload().asText())
                            .isEqualTo("accepted");
                    fakeDelivery.set(new DeliveredMail(mail.to(), mail.subject(), mail.body()));
                },
                fakeDelivery::get,
                expected
        );

        assertThat(script.remaining()).isZero();
        assertThat(ledger.entries()).satisfiesExactly(
                call -> assertSimulatedEmailCall(call, "production_send", 1),
                call -> assertSimulatedEmailCall(call, "fake_send", 2)
        );
        assertThat(ledger.simulatedCallCount()).isEqualTo(2);
        assertThat(ledger.liveCallCount()).isZero();
    }

    @Test
    void unregisteredProductionSmtpTargetIsDeniedBeforeDns() {
        EmailServiceImpl production = new EmailServiceImpl(
                mailSender("smtp-provider.invalid", 2525),
                null,
                enabledEmailProperties()
        );

        assertThatThrownBy(() -> production.sendSimpleEmail(
                "recipient@fixture.invalid", "blocked", "must not leave the process"
        )).isInstanceOf(RuntimeException.class)
                .hasRootCauseInstanceOf(UnexpectedExternalCall.class);
        ExternalCall blocked = offlineNetwork.ledger().entries().get(0);
        assertThat(offlineNetwork.ledger().entries()).containsExactly(blocked);
        assertThat(blocked).satisfies(call -> {
            assertThat(call.boundary()).isEqualTo("network");
            assertThat(call.live()).isFalse();
            assertThat(call.operation()).isEqualTo("resolve");
            assertThat(call.outcome()).isEqualTo("blocked");
            assertThat(call.phase()).isEqualTo("PRE_DNS");
            assertThat(call.simulated()).isFalse();
            assertThat(call.target()).isEqualTo("smtp-provider.invalid:0");
        });
        offlineNetwork.acknowledgeBlockedCall(
                blocked, "network", "resolve", "PRE_DNS", "smtp-provider.invalid"
        );
    }

    private static void assertSimulatedEmailCall(
            ExternalCall call,
            String operation,
            long sequence
    ) {
        assertThat(call.boundary()).isEqualTo("email");
        assertThat(call.live()).isFalse();
        assertThat(call.operation()).isEqualTo(operation);
        assertThat(call.outcome()).isEqualTo("response");
        assertThat(call.phase()).isEqualTo("SIMULATED");
        assertThat(call.sequence()).isEqualTo(sequence);
        assertThat(call.simulated()).isTrue();
        assertThat(call.target()).isEqualTo("fake-smtp:2525");
    }

    private static void assertDeliveryContract(
            Delivery delivery,
            DeliveryObservation observation,
            ExpectedMail expected
    ) throws Exception {
        delivery.send(expected);
        DeliveredMail actual = observation.read();
        assertThat(actual.to()).isEqualTo(expected.to());
        assertThat(actual.subject()).isEqualTo(expected.subject());
        assertThat(actual.body()).contains(expected.body());
    }

    private static JavaMailSenderImpl mailSender(String host, int port) {
        JavaMailSenderImpl sender = new JavaMailSenderImpl();
        sender.setHost(host);
        sender.setPort(port);
        sender.setProtocol("smtp");
        Properties properties = sender.getJavaMailProperties();
        properties.setProperty("mail.smtp.auth", "false");
        properties.setProperty("mail.smtp.starttls.enable", "false");
        properties.setProperty("mail.from", "noreply@fixture.invalid");
        properties.setProperty("mail.smtp.localhost", "127.0.0.1");
        properties.setProperty("mail.smtp.localaddress", "127.0.0.1");
        properties.setProperty("mail.smtp.connectiontimeout", "1000");
        properties.setProperty("mail.smtp.timeout", "1000");
        properties.setProperty("mail.smtp.writetimeout", "1000");
        return sender;
    }

    private static EmailProperties enabledEmailProperties() {
        EmailProperties properties = new EmailProperties();
        properties.setEnabled(true);
        properties.setFrom("noreply@fixture.invalid");
        properties.setFromName("CodeCrow Offline Fixture");
        return properties;
    }

    @FunctionalInterface
    private interface Delivery {
        void send(ExpectedMail mail);
    }

    @FunctionalInterface
    private interface DeliveryObservation {
        DeliveredMail read() throws Exception;
    }

    private record ExpectedMail(String to, String subject, String body) {
    }

    private record DeliveredMail(String to, String subject, String body) {
    }

    private record RecordedSmtpMessage(String envelopeRecipient, String rawMessage) {

        private DeliveredMail delivery() throws IOException {
            return new DeliveredMail(envelopeRecipient, header("Subject"), body());
        }

        private String header(String name) throws IOException {
            int bodySeparator = rawMessage.indexOf("\r\n\r\n");
            if (bodySeparator < 0) {
                throw new IOException("fixture received SMTP DATA without a header terminator");
            }
            String prefix = name + ":";
            for (String line : rawMessage.substring(0, bodySeparator).split("\r\n")) {
                if (line.regionMatches(true, 0, prefix, 0, prefix.length())) {
                    return line.substring(prefix.length()).trim();
                }
            }
            throw new IOException("fixture received SMTP DATA without a " + name + " header");
        }

        private String body() throws IOException {
            int bodySeparator = rawMessage.indexOf("\r\n\r\n");
            if (bodySeparator < 0) {
                throw new IOException("fixture received SMTP DATA without a header terminator");
            }
            return rawMessage.substring(bodySeparator + 4);
        }
    }

    /** One-message SMTP fixture that never asks the JVM for a host name. */
    private static final class LiteralSmtpFixture implements AutoCloseable {

        private final ServerSocket listener;
        private final FutureTask<RecordedSmtpMessage> exchange;

        private LiteralSmtpFixture() throws IOException {
            listener = new ServerSocket();
            listener.bind(new InetSocketAddress(
                    InetAddress.getByAddress(new byte[]{127, 0, 0, 1}),
                    0
            ));
            listener.setSoTimeout(5_000);
            exchange = new FutureTask<>(this::serve);
            Thread serverThread = new Thread(exchange, "email-literal-smtp-fixture");
            serverThread.setDaemon(true);
            serverThread.start();
        }

        private int port() {
            return listener.getLocalPort();
        }

        private RecordedSmtpMessage awaitMessage() throws Exception {
            try {
                return exchange.get(5, TimeUnit.SECONDS);
            } catch (ExecutionException failure) {
                if (failure.getCause() instanceof Exception exception) {
                    throw exception;
                }
                throw new AssertionError("literal SMTP fixture failed", failure.getCause());
            }
        }

        private RecordedSmtpMessage serve() throws Exception {
            try (Socket client = listener.accept()) {
                client.setSoTimeout(5_000);
                BufferedReader reader = new BufferedReader(new InputStreamReader(
                        client.getInputStream(), StandardCharsets.US_ASCII
                ));
                BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(
                        client.getOutputStream(), StandardCharsets.US_ASCII
                ));

                reply(writer, "220 127.0.0.1 ESMTP offline fixture");
                expectPrefix(reader, "EHLO ");
                reply(writer, "250 127.0.0.1");
                expectPrefix(reader, "MAIL FROM:");
                reply(writer, "250 sender accepted");
                String recipient = envelopePath(expectPrefix(reader, "RCPT TO:"), "RCPT TO:");
                reply(writer, "250 recipient accepted");
                expectExact(reader, "DATA");
                reply(writer, "354 end data with <CRLF>.<CRLF>");

                StringBuilder rawMessage = new StringBuilder();
                while (true) {
                    String line = requiredLine(reader);
                    if (line.equals(".")) {
                        break;
                    }
                    if (line.startsWith("..")) {
                        line = line.substring(1);
                    }
                    rawMessage.append(line).append("\r\n");
                }
                reply(writer, "250 message accepted");
                expectExact(reader, "QUIT");
                reply(writer, "221 127.0.0.1 closing connection");
                return new RecordedSmtpMessage(recipient, rawMessage.toString());
            }
        }

        private static String expectPrefix(BufferedReader reader, String expectedPrefix) throws IOException {
            String command = requiredLine(reader);
            if (!command.regionMatches(true, 0, expectedPrefix, 0, expectedPrefix.length())) {
                throw new IOException("fixture expected " + expectedPrefix + " but received " + command);
            }
            return command;
        }

        private static void expectExact(BufferedReader reader, String expected) throws IOException {
            String command = requiredLine(reader);
            if (!command.equalsIgnoreCase(expected)) {
                throw new IOException("fixture expected " + expected + " but received " + command);
            }
        }

        private static String requiredLine(BufferedReader reader) throws IOException {
            String line = reader.readLine();
            if (line == null) {
                throw new IOException("fixture SMTP client closed the dialogue early");
            }
            return line;
        }

        private static String envelopePath(String command, String prefix) throws IOException {
            String path = command.substring(prefix.length()).trim();
            if (path.length() < 3 || path.charAt(0) != '<' || path.charAt(path.length() - 1) != '>') {
                throw new IOException("fixture received a malformed SMTP envelope path");
            }
            return path.substring(1, path.length() - 1);
        }

        private static void reply(BufferedWriter writer, String response) throws IOException {
            writer.write(response);
            writer.write("\r\n");
            writer.flush();
        }

        @Override
        public void close() throws IOException {
            listener.close();
        }
    }
}
