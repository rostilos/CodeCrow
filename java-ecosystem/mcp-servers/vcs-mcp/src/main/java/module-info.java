module org.rostilos.codecrow.mcpservers {
    requires org.apache.tomcat.embed.core;
    requires io.github.cdimascio.dotenv.java;
    requires io.modelcontextprotocol.sdk.mcp;
    requires reactor.core;
    requires org.slf4j;
    requires org.rostilos.codecrow.vcs;
    requires com.fasterxml.jackson.databind;
    requires okhttp3;
    requires jtokkit;
    requires jdk.httpserver;
}