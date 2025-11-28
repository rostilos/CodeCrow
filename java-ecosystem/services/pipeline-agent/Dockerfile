FROM maven:latest AS builder

WORKDIR /app

COPY ./target/codecrow-pipeline-agent-1.0.jar ./codecrow-pipeline-agent-1.0.jar

FROM openjdk:17.0.2-jdk-slim-bullseye


RUN apt-get update && \
    apt-get install -y curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -r appgroup && useradd -r -g appgroup appuser
RUN mkdir -p /app/logs && chown -R appuser:appgroup /app

WORKDIR /app

# Copy the built jar file
#COPY ./codecrow-pipeline-agent/mcp-server ./codecrow-pipeline-agent/mcp-server
COPY --from=builder /app/codecrow-pipeline-agent-1.0.jar app.jar

# Change ownership
RUN chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8082

# JVM options
ENV JAVA_OPTS="-Xms512m -Xmx1g -XX:+UseG1GC -XX:G1HeapRegionSize=16m -XX:+UseStringDeduplication"

# Run the application
CMD ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
