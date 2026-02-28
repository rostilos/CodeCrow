FROM eclipse-temurin:17-jre-alpine

RUN apk add --no-cache curl && \
    addgroup -g 1001 -S appgroup && adduser -u 1001 -S appuser -G appgroup && \
    mkdir -p /app/logs && chown -R appuser:appgroup /app

WORKDIR /app

COPY --chown=appuser:appgroup ./target/codecrow-pipeline-agent-1.0.jar app.jar

USER appuser

EXPOSE 8082

ENV JAVA_OPTS="-Xms512m -Xmx1g -XX:+UseG1GC -XX:G1HeapRegionSize=16m -XX:+UseStringDeduplication"

CMD ["sh", "-c", "java $JAVA_OPTS -jar app.jar"]
