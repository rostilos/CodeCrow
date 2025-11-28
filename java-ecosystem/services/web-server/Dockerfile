FROM maven:latest AS builder

WORKDIR /app

COPY ./target/codecrow-web-server-1.0.jar ./codecrow-web-server-1.0.jar

FROM openjdk:17.0.2-jdk-slim-bullseye

RUN apt-get update && \
    apt-get install -y curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -r appgroup && useradd -r -g appgroup appuser
RUN mkdir -p /app/logs && chown -R appuser:appgroup /app

WORKDIR /app

# Copy the built jar file
COPY --from=builder /app/codecrow-web-server-1.0.jar app.jar

# Change ownership
RUN chown appuser:appgroup app.jar

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8081

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8081/actuator/health || exit 1

# JVM options
ENV JAVA_OPTS="-Xms512m -Xmx1g -XX:+UseG1GC -XX:G1HeapRegionSize=16m -XX:+UseStringDeduplication"

# Run the application
CMD ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
