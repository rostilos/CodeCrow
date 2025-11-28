# Deployment Guide

Production deployment guide for CodeCrow.

## Prerequisites

- Linux server (Ubuntu 20.04+ or similar)
- Docker 20.10+
- Docker Compose v2.0+
- Domain name with DNS configured
- SSL certificate (Let's Encrypt recommended)
- 8GB+ RAM
- 4+ CPU cores
- 100GB+ disk space

## Pre-Deployment Checklist

- [ ] Server provisioned and accessible
- [ ] Docker and Docker Compose installed
- [ ] Domain DNS configured
- [ ] SSL certificates obtained
- [ ] OpenRouter API key obtained
- [ ] Bitbucket workspace access configured
- [ ] Firewall rules planned
- [ ] Backup strategy defined

## Installation Steps

### 1. Clone Repository

```bash
git clone <repository-url> /opt/codecrow
cd /opt/codecrow
```

### 2. Configure Environment

```bash
# Copy sample configurations
cp deployment/docker-compose-sample.yml deployment/docker-compose.yml
cp deployment/config/java-shared/application.properties.sample \
   deployment/config/java-shared/application.properties
cp deployment/config/mcp-client/.env.sample \
   deployment/config/mcp-client/.env
cp deployment/config/rag-pipeline/.env.sample \
   deployment/config/rag-pipeline/.env
cp deployment/config/web-frontend/.env.sample \
   deployment/config/web-frontend/.env
```

### 3. Generate Secrets

```bash
# Generate JWT secret (256-bit)
JWT_SECRET=$(openssl rand -base64 32)
echo "JWT Secret: $JWT_SECRET"

# Generate encryption key (256-bit)
ENCRYPTION_KEY=$(openssl rand -base64 32)
echo "Encryption Key: $ENCRYPTION_KEY"

# Generate strong database password
DB_PASSWORD=$(openssl rand -base64 24)
echo "Database Password: $DB_PASSWORD"
```

**Store these securely** - you'll need them for configuration.

### 4. Update Configuration

**deployment/config/java-shared/application.properties**:
```properties
codecrow.security.jwtSecret=<JWT_SECRET>
codecrow.security.encryption-key=<ENCRYPTION_KEY>
codecrow.web.base.url=https://codecrow.example.com
codecrow.mcp.client.url=http://mcp-client:8000/review
codecrow.rag.api.url=http://rag-pipeline:8001
```

**deployment/config/rag-pipeline/.env**:
```bash
OPENROUTER_API_KEY=sk-or-v1-your-actual-key
QDRANT_URL=http://qdrant:6333
```

**deployment/config/web-frontend/.env**:
```bash
VITE_API_URL=https://codecrow.example.com/api
VITE_WEBHOOK_URL=https://codecrow.example.com/webhook
```

**deployment/docker-compose.yml**:

Update database credentials:
```yaml
postgres:
  environment:
    POSTGRES_PASSWORD: <DB_PASSWORD>

web-server:
  environment:
    SPRING_DATASOURCE_PASSWORD: <DB_PASSWORD>

pipeline-agent:
  environment:
    SPRING_DATASOURCE_PASSWORD: <DB_PASSWORD>
```

### 5. Build and Start Services

```bash
cd /opt/codecrow
./tools/production-build.sh
```

This script:
- Builds Java artifacts
- Copies MCP servers JAR
- Starts all Docker containers
- Waits for services to be healthy

### 6. Verify Services

```bash
cd deployment
docker compose ps
```

All services should show status "Up (healthy)".

```bash
# Check logs
docker compose logs -f web-server
docker compose logs -f pipeline-agent
```

### 7. Setup Reverse Proxy

#### Nginx Configuration

Create `/etc/nginx/sites-available/codecrow`:

```nginx
# Frontend
server {
    listen 80;
    listen [::]:80;
    server_name codecrow.example.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name codecrow.example.com;

    ssl_certificate /etc/letsencrypt/live/codecrow.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/codecrow.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Frontend
    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # API
    location /api {
        proxy_pass http://localhost:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Increase timeouts for long-running requests
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    # Webhooks (restrict to Bitbucket IPs)
    location /webhook {
        # Bitbucket Cloud IP ranges
        allow 104.192.136.0/21;
        allow 185.166.140.0/22;
        deny all;

        proxy_pass http://localhost:8082;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        proxy_read_timeout 600s;
    }
}
```

Enable site:
```bash
ln -s /etc/nginx/sites-available/codecrow /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

#### SSL with Let's Encrypt

```bash
apt-get install certbot python3-certbot-nginx
certbot --nginx -d codecrow.example.com
```

### 8. Configure Firewall

```bash
# Allow SSH
ufw allow 22/tcp

# Allow HTTP/HTTPS
ufw allow 80/tcp
ufw allow 443/tcp

# Block direct access to services
ufw deny 5432/tcp  # PostgreSQL
ufw deny 6379/tcp  # Redis
ufw deny 6333/tcp  # Qdrant
ufw deny 8000/tcp  # MCP Client
ufw deny 8001/tcp  # RAG Pipeline
ufw deny 8080/tcp  # Frontend
ufw deny 8081/tcp  # Web Server
ufw deny 8082/tcp  # Pipeline Agent

# Enable firewall
ufw enable
```

### 9. Create First Admin User

Connect to database:
```bash
docker exec -it codecrow-postgres psql -U codecrow_user -d codecrow_ai
```

Create admin user:
```sql
-- Generate password hash (use bcrypt generator or Spring Boot)
-- Example password: admin123 (change this!)
INSERT INTO "user" (id, username, email, password, roles, created_at)
VALUES (
  gen_random_uuid(),
  'admin',
  'admin@example.com',
  '$2a$10$encrypted_password_here',
  ARRAY['USER', 'ADMIN'],
  NOW()
);
```

Or use Spring Boot's password encoder programmatically.

### 10. Setup Monitoring

#### Docker Health Monitoring

Create `/opt/codecrow/scripts/health-check.sh`:
```bash
#!/bin/bash

SERVICES="codecrow-postgres codecrow-redis codecrow-qdrant codecrow-web-application codecrow-pipeline-agent codecrow-mcp-client codecrow-rag-pipeline codecrow-web-frontend"

for service in $SERVICES; do
    if ! docker ps | grep -q $service; then
        echo "ALERT: $service is down!"
        # Send alert (email, Slack, etc.)
    fi
done
```

Add to crontab:
```bash
*/5 * * * * /opt/codecrow/scripts/health-check.sh
```

#### Log Rotation

Configure log rotation in `/etc/logrotate.d/codecrow`:
```
/var/lib/docker/volumes/web_logs/_data/*.log
/var/lib/docker/volumes/pipeline_agent_logs/_data/*.log
/var/lib/docker/volumes/web_frontend_logs/_data/*.log
/var/lib/docker/volumes/rag_logs/_data/*.log
{
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    missingok
    copytruncate
}
```

### 11. Backup Configuration

Create backup script `/opt/codecrow/scripts/backup.sh`:
```bash
#!/bin/bash

BACKUP_DIR=/backups/codecrow
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Database backup
docker exec codecrow-postgres pg_dump -U codecrow_user codecrow_ai | \
  gzip > $BACKUP_DIR/db_$DATE.sql.gz

# Qdrant backup
docker exec codecrow-qdrant tar czf - /qdrant/storage | \
  cat > $BACKUP_DIR/qdrant_$DATE.tar.gz

# Configuration backup
tar czf $BACKUP_DIR/config_$DATE.tar.gz /opt/codecrow/deployment/config

# Cleanup old backups (keep 30 days)
find $BACKUP_DIR -name "*.gz" -mtime +30 -delete

echo "Backup completed: $DATE"
```

Schedule daily backups:
```bash
0 2 * * * /opt/codecrow/scripts/backup.sh
```

## Production Configuration Tuning

### Database

**deployment/docker-compose.yml** - PostgreSQL:
```yaml
postgres:
  command:
    - postgres
    - -c
    - max_connections=200
    - -c
    - shared_buffers=256MB
    - -c
    - effective_cache_size=1GB
    - -c
    - work_mem=8MB
```

### Java Services

**deployment/config/java-shared/application.properties**:
```properties
# Production settings
spring.jpa.hibernate.ddl-auto=validate
spring.jpa.show-sql=false
logging.level.org.rostilos.codecrow=INFO

# Connection pool
spring.datasource.hikari.maximum-pool-size=20
spring.datasource.hikari.minimum-idle=10
spring.datasource.hikari.connection-timeout=30000
```

**deployment/docker-compose.yml** - JVM options:
```yaml
web-server:
  environment:
    JAVA_OPTS: "-Xmx2G -Xms1G -XX:+UseG1GC"

pipeline-agent:
  environment:
    JAVA_OPTS: "-Xmx2G -Xms1G -XX:+UseG1GC"
```

### Resource Limits

```yaml
services:
  web-server:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 3G
        reservations:
          cpus: '1.0'
          memory: 2G

  pipeline-agent:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 3G
        reservations:
          cpus: '1.0'
          memory: 2G

  mcp-client:
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 1G

  rag-pipeline:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
        reservations:
          cpus: '1.0'
          memory: 2G
```

## Security Hardening

### 1. Secrets Management

Use Docker secrets or external secret manager:

```yaml
secrets:
  db_password:
    file: ./secrets/db_password.txt
  jwt_secret:
    file: ./secrets/jwt_secret.txt

services:
  web-server:
    secrets:
      - db_password
      - jwt_secret
```

### 2. Network Isolation

```yaml
networks:
  frontend:
  backend:
  internal:

services:
  web-frontend:
    networks:
      - frontend
  
  web-server:
    networks:
      - frontend
      - backend
  
  postgres:
    networks:
      - internal
```

### 3. Read-Only Root Filesystem

```yaml
services:
  web-server:
    read_only: true
    tmpfs:
      - /tmp
      - /app/logs
```

### 4. Run as Non-Root

Update Dockerfiles:
```dockerfile
RUN groupadd -r codecrow && useradd -r -g codecrow codecrow
USER codecrow
```

### 5. Security Scanning

```bash
# Scan images for vulnerabilities
docker scan codecrow-web-server
docker scan codecrow-pipeline-agent
```

## Scaling

### Horizontal Scaling

**Web Server** (stateless):
```yaml
web-server:
  deploy:
    replicas: 3
```

Use load balancer (nginx, HAProxy) to distribute traffic.

**Pipeline Agent** (use queue-based distribution):
- Setup message queue (RabbitMQ, Redis Queue)
- Multiple workers consume from queue
- Each worker acquires lock before processing

### Vertical Scaling

Increase resources in docker-compose.yml or use Kubernetes for auto-scaling.

## Monitoring & Observability

### Prometheus + Grafana

Add monitoring stack:
```yaml
prometheus:
  image: prom/prometheus
  volumes:
    - ./prometheus.yml:/etc/prometheus/prometheus.yml
  ports:
    - "9090:9090"

grafana:
  image: grafana/grafana
  ports:
    - "3000:3000"
  volumes:
    - grafana_data:/var/lib/grafana
```

Spring Boot Actuator exposes metrics at `/actuator/prometheus`.

### Application Logs

Centralized logging with ELK stack or Loki + Grafana.

### Alerts

Setup alerts for:
- Service down
- High error rate
- Database connection failures
- Disk space low
- High memory usage
- Analysis failures

## Troubleshooting Production Issues

### Service Won't Start

```bash
docker compose logs <service-name>
docker inspect <container-name>
```

### Database Connection Issues

```bash
docker exec -it codecrow-postgres psql -U codecrow_user -d codecrow_ai
# Test queries
SELECT version();
SELECT count(*) FROM "user";
```

### High Memory Usage

```bash
docker stats
# Adjust memory limits in docker-compose.yml
```

### Slow Analysis

- Check RAG indexing performance
- Monitor OpenRouter API latency
- Verify network connectivity
- Check database query performance

### Webhook Not Received

- Verify firewall allows Bitbucket IPs
- Check nginx logs: `tail -f /var/log/nginx/access.log`
- Verify webhook configuration in Bitbucket
- Check project token is valid

## Disaster Recovery

### Full System Recovery

1. Restore configuration files
2. Restore database from backup
3. Restore Qdrant data
4. Start services
5. Verify functionality

```bash
# Restore database
gunzip < /backups/codecrow/db_20240115.sql.gz | \
  docker exec -i codecrow-postgres psql -U codecrow_user -d codecrow_ai

# Restore Qdrant
docker exec codecrow-qdrant rm -rf /qdrant/storage/*
gunzip < /backups/codecrow/qdrant_20240115.tar.gz | \
  docker exec -i codecrow-qdrant tar xzf - -C /
```

## Maintenance

### Update Application

```bash
cd /opt/codecrow
git pull
./tools/production-build.sh
```

### Database Maintenance

```bash
docker exec codecrow-postgres psql -U codecrow_user -d codecrow_ai -c "VACUUM ANALYZE;"
```

### Clear Old Data

```sql
-- Remove old resolved issues
DELETE FROM branch_issue WHERE resolved = TRUE AND resolved_at < NOW() - INTERVAL '90 days';
```

### Update Dependencies

```bash
# Java
cd java-ecosystem
mvn versions:display-dependency-updates

# Python
cd python-ecosystem/mcp-client
pip list --outdated
```

## Cost Optimization

### OpenRouter

- Monitor API usage
- Use cheaper models for non-critical analysis
- Cache embeddings where possible
- Implement rate limiting

### Infrastructure

- Right-size server resources
- Use spot instances for non-production
- Implement data retention policies
- Compress logs and backups

