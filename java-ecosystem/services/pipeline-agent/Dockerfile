FROM eclipse-temurin:17-jre-alpine

RUN apk add --no-cache curl su-exec && \
    addgroup -g 1001 -S appgroup && adduser -u 1001 -S appuser -G appgroup && \
    mkdir -p /app/logs && chown -R appuser:appgroup /app

WORKDIR /app

COPY --chown=appuser:appgroup ./target/codecrow-pipeline-agent-1.0.jar app.jar

# Entrypoint: fix volume ownership, then drop to appuser
RUN printf '#!/bin/sh\nchown -R appuser:appgroup /app/logs 2>/dev/null || true\nexec su-exec appuser "$@"\n' > /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://127.0.0.1:8082/actuator/health || exit 1

ENV JAVA_OPTS="-Xms512m -Xmx1g -XX:+UseG1GC -XX:G1HeapRegionSize=16m -XX:+UseStringDeduplication"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
