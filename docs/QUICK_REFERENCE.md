# CodeCrow Quick Reference

Fast reference guide for common tasks and commands.

## Service URLs

| Service | URL | Access |
|---------|-----|--------|
| Frontend | http://localhost:8080 | Public |
| Web API | http://localhost:8081 | Public |
| Swagger | http://localhost:8081/swagger-ui-custom.html | Public |
| Pipeline Agent | http://localhost:8082 | Webhook only |
| MCP Client | http://localhost:8000 | Internal |
| RAG Pipeline | http://localhost:8001 | Internal |

## Quick Commands

### Start Services
```bash
cd /opt/codecrow
./tools/production-build.sh
```

### Stop Services
```bash
cd deployment
docker compose down
```

### View Logs
```bash
docker compose logs -f <service-name>
# Examples:
docker compose logs -f web-server
docker compose logs -f pipeline-agent
```

### Restart Service
```bash
docker compose restart <service-name>
```

### Database Access
```bash
docker exec -it codecrow-postgres psql -U codecrow_user -d codecrow_ai
```

### Check Service Health
```bash
curl http://localhost:8081/actuator/health
curl http://localhost:8000/health
curl http://localhost:8001/health
```

## Configuration Files

| File | Purpose |
|------|---------|
| `deployment/config/java-shared/application.properties` | Java services config |
| `deployment/config/mcp-client/.env` | MCP client settings |
| `deployment/config/rag-pipeline/.env` | RAG pipeline settings |
| `deployment/config/web-frontend/.env` | Frontend settings |
| `deployment/docker-compose.yml` | Container orchestration |

## Generate Secrets

```bash
# JWT Secret
openssl rand -base64 32

# Encryption Key
openssl rand -base64 32

# Database Password
openssl rand -base64 24
```

## Common Tasks

### Create Admin User
```sql
INSERT INTO "user" (id, username, email, password, roles, created_at)
VALUES (
  gen_random_uuid(),
  'admin',
  'admin@example.com',
  '$2a$10$hashed_password',
  ARRAY['USER', 'ADMIN'],
  NOW()
);
```

### Reset Database
```bash
docker exec -it codecrow-postgres psql -U codecrow_user -c "DROP DATABASE codecrow_ai;"
docker exec -it codecrow-postgres psql -U codecrow_user -c "CREATE DATABASE codecrow_ai;"
docker compose restart web-server pipeline-agent
```

### Clear Analysis Locks
```sql
DELETE FROM analysis_lock WHERE locked_at < NOW() - INTERVAL '30 minutes';
```

### View Active Issues
```sql
SELECT b.name, COUNT(*) 
FROM branch_issue bi 
JOIN branch b ON bi.branch_id = b.id 
WHERE bi.resolved = FALSE 
GROUP BY b.name;
```

### Backup Database
```bash
docker exec codecrow-postgres pg_dump -U codecrow_user codecrow_ai | gzip > backup_$(date +%Y%m%d).sql.gz
```

### Restore Database
```bash
gunzip < backup_20240115.sql.gz | docker exec -i codecrow-postgres psql -U codecrow_user -d codecrow_ai
```

## Development

### Run Java Service Locally
```bash
cd java-ecosystem/services/web-server
mvn spring-boot:run
```

### Run Python Service Locally
```bash
cd python-ecosystem/mcp-client
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

### Run Frontend Locally
```bash
cd frontend
npm run dev  # or: bun run dev
```

### Build Java Artifacts
```bash
cd java-ecosystem
mvn clean package -DskipTests
```

### Run Tests
```bash
# Java
mvn test

# Python
pytest

# Frontend
npm test
```

## API Quick Reference

### Login
```bash
curl -X POST http://localhost:8081/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"user@example.com","password":"password"}'
```

### Create Workspace
```bash
curl -X POST http://localhost:8081/api/workspaces \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name":"My Workspace","description":"..."}'
```

### Trigger Webhook (Test)
```bash
curl -X POST http://localhost:8082/api/v1/bitbucket-cloud/webhook \
  -H "Authorization: Bearer <project-token>" \
  -H "Content-Type: application/json" \
  -d @sample-webhook.json
```

## Troubleshooting

### Service Won't Start
```bash
docker compose logs <service-name>
docker compose restart <service-name>
```

### High Memory Usage
```bash
docker stats
# Adjust limits in docker-compose.yml
```

### Database Connection Issues
```bash
docker exec -it codecrow-postgres psql -U codecrow_user -d codecrow_ai -c "SELECT version();"
```

### Clear Redis Cache
```bash
docker exec codecrow-redis redis-cli FLUSHDB
```

### Check Qdrant Collections
```bash
curl http://localhost:6333/collections
```

## Environment Variables

### Java Services
```properties
SPRING_DATASOURCE_URL=jdbc:postgresql://postgres:5432/codecrow_ai
SPRING_DATASOURCE_USERNAME=codecrow_user
SPRING_DATASOURCE_PASSWORD=codecrow_pass
SERVER_PORT=8081
```

### Python Services
```bash
OPENROUTER_API_KEY=sk-or-v1-...
RAG_ENABLED=true
QDRANT_URL=http://localhost:6333
```

### Frontend
```bash
VITE_API_URL=http://localhost:8081/api
VITE_WEBHOOK_URL=http://localhost:8082
```

## File Locations

### Logs
- Web Server: `/var/lib/docker/volumes/web_logs/_data/`
- Pipeline Agent: `/var/lib/docker/volumes/pipeline_agent_logs/_data/`
- RAG Pipeline: `/var/lib/docker/volumes/rag_logs/_data/`

### Data
- PostgreSQL: `/var/lib/docker/volumes/postgres_data/_data/`
- Qdrant: `/var/lib/docker/volumes/qdrant_data/_data/`
- Redis: `/var/lib/docker/volumes/redis_data/_data/`

## Network

### Bitbucket IP Ranges (for firewall)
```
104.192.136.0/21
185.166.140.0/22
```

### Container Network
All services on `codecrow-network` can communicate by service name.

## Performance

### Database Maintenance
```sql
VACUUM ANALYZE;
REINDEX DATABASE codecrow_ai;
```

### Clean Old Data
```sql
DELETE FROM branch_issue WHERE resolved = TRUE AND resolved_at < NOW() - INTERVAL '90 days';
DELETE FROM code_analysis WHERE completed_at < NOW() - INTERVAL '180 days';
```

## Security

### Change Database Password
1. Update in `docker-compose.yml` for all services
2. Restart services:
```bash
docker compose down
docker compose up -d
```

### Rotate JWT Secret
1. Update `application.properties`
2. Restart Java services
3. All users must re-login

## Monitoring

### Check All Services
```bash
docker compose ps
```

### Resource Usage
```bash
docker stats
```

### Database Size
```sql
SELECT pg_size_pretty(pg_database_size('codecrow_ai'));
```

### Active Connections
```sql
SELECT count(*) FROM pg_stat_activity WHERE datname = 'codecrow_ai';
```

## Links

- [Full Documentation](README.md)
- [Getting Started](02-getting-started.md)
- [Troubleshooting](11-troubleshooting.md)
- [API Reference](06-api-reference.md)

