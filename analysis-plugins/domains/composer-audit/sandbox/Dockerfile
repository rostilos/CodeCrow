FROM alpine:3.22
RUN apk add --no-cache php84 php84-phar php84-openssl php84-mbstring php84-iconv php84-curl \
      ca-certificates python3 iptables util-linux curl && \
    ln -s /usr/bin/php84 /usr/local/bin/php && \
    addgroup -g 1001 -S audit && adduser -u 1001 -S audit -G audit && \
    mkdir -p /opt/composer /opt/audit /work && chown audit:audit /work
# A fixed, checksum-verified upstream PHAR, never a repository-supplied executable.
RUN curl -fsSL https://getcomposer.org/download/2.11.1/composer.phar -o /opt/composer/composer.phar && \
    echo '6f1b891e1d4927d62740276c540fb490e47689e19e102c49e8da849794882f8c  /opt/composer/composer.phar' | sha256sum -c -
COPY audit.py server.py entrypoint.sh /opt/audit/
RUN chmod 0555 /opt/audit/entrypoint.sh
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /work
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=2)"
ENTRYPOINT ["/opt/audit/entrypoint.sh"]
