FROM eclipse-temurin:17-jre-alpine

RUN apk add --no-cache curl git su-exec && \
    addgroup -g 1001 -S appgroup && adduser -u 1001 -S appuser -G appgroup && \
    mkdir -p /app/logs /app/config /app/codecrow-config /run/codecrow-config && chown -R appuser:appgroup /app

WORKDIR /app

COPY --chown=appuser:appgroup java-ecosystem/services/pipeline-agent/target/codecrow-pipeline-agent-1.0.jar app.jar
COPY --chown=appuser:appgroup analysis-plugins/build/java/ /app/plugins/

COPY java-ecosystem/services/pipeline-agent/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -f http://127.0.0.1:8082/actuator/health || exit 1

ENV JAVA_OPTS="-Xms512m -Xmx1g -XX:+UseG1GC -XX:G1HeapRegionSize=16m -XX:+UseStringDeduplication"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sh", "-c", "java $JAVA_OPTS -Dloader.path=/app/plugins -jar app.jar"]
