package org.rostilos.codecrow.mcp;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.InputStream;

import javax.xml.parsers.DocumentBuilderFactory;

import org.junit.jupiter.api.Test;
import org.w3c.dom.Element;

class LoggingConfigurationTest {

    @Test
    void sendsAllProcessLogsToStderrOnly() throws Exception {
        try (InputStream configuration = getClass().getResourceAsStream("/logback.xml")) {
            assertThat(configuration).isNotNull();

            var document = DocumentBuilderFactory.newInstance()
                    .newDocumentBuilder()
                    .parse(configuration);
            var appenders = document.getElementsByTagName("appender");

            assertThat(appenders.getLength()).isEqualTo(1);
            Element appender = (Element) appenders.item(0);
            assertThat(appender.getAttribute("name")).isEqualTo("STDERR");
            assertThat(appender.getAttribute("class"))
                    .isEqualTo("ch.qos.logback.core.ConsoleAppender");
            assertThat(appender.getElementsByTagName("target").item(0).getTextContent().trim())
                    .isEqualTo("System.err");

            var appenderReferences = document.getElementsByTagName("appender-ref");
            assertThat(appenderReferences.getLength()).isEqualTo(1);
            assertThat(((Element) appenderReferences.item(0)).getAttribute("ref"))
                    .isEqualTo("STDERR");
        }
    }
}
