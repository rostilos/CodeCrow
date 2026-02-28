FROM eclipse-temurin:17-jre-alpine

RUN apk add --no-cache curl && \
    addgroup -g 1001 -S appgroup && adduser -u 1001 -S appuser -G appgroup && \
    mkdir -p /app/logs && chown -R appuser:appgroup /app

WORKDIR /app

COPY --chown=appuser:appgroup ./target/codecrow-web-server-1.0-exec.jar app.jar

USER appuser

EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://localhost:8081/actuator/health || exit 1

ENV JAVA_OPTS="-Xms512m -Xmx1g -XX:+UseG1GC -XX:G1HeapRegionSize=16m -XX:+UseStringDeduplication"

CMD ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
