# Troubleshooting

Common issues and solutions for CodeCrow.

## Installation & Setup Issues

### Docker Compose Fails to Start

**Symptom**: Services fail to start or keep restarting.

**Solutions**:

1. **Check logs**:
```bash
docker compose logs <service-name>
```

2. **Insufficient resources**:
```bash
# Increase Docker memory limit (Docker Desktop)
# Settings → Resources → Memory (set to 8GB+)
```

3. **Port conflicts**:
```bash
# Check if ports are already in use
lsof -i :8080
lsof -i :8081
# Kill conflicting processes or change ports
```

4. **Permission issues**:
```bash
# Fix volume permissions
docker compose down
docker volume rm source_code_tmp
docker compose up -d
```

### Configuration Files Not Found

**Symptom**: "Configuration file not found" errors.

**Solution**:
```bash
# Ensure all config files are copied from samples
cp deployment/config/java-shared/application.properties.sample \
   deployment/config/java-shared/application.properties
cp deployment/config/mcp-client/.env.sample \
   deployment/config/mcp-client/.env
cp deployment/config/rag-pipeline/.env.sample \
   deployment/config/rag-pipeline/.env
cp deployment/config/web-frontend/.env.sample \
   deployment/config/web-frontend/.env
```

### Database Connection Failed

**Symptom**: Services can't connect to PostgreSQL.

**Checks**:

1. **Verify PostgreSQL is running**:
```bash
docker ps | grep postgres
docker logs codecrow-postgres
```

2. **Check credentials match**:
```yaml
# docker-compose.yml
postgres:
  environment:
    POSTGRES_PASSWORD: codecrow_pass

web-server:
  environment:
    SPRING_DATASOURCE_PASSWORD: codecrow_pass
```

3. **Check database exists**:
```bash
docker exec -it codecrow-postgres psql -U codecrow_user -l
```

4. **Create database if missing**:
```bash
docker exec -it codecrow-postgres psql -U codecrow_user -c "CREATE DATABASE codecrow_ai;"
```

## Service-Specific Issues

### Web Server Won't Start

**Check health**:
```bash
docker logs codecrow-web-application
curl http://localhost:8081/actuator/health
```

**Common issues**:

1. **JWT secret not set**:
```properties
# application.properties
codecrow.security.jwtSecret=<valid-base64-string>
```

2. **Database schema mismatch**:
```bash
# Reset database (data loss!)
docker exec -it codecrow-postgres psql -U codecrow_user -c "DROP DATABASE codecrow_ai;"
docker exec -it codecrow-postgres psql -U codecrow_user -c "CREATE DATABASE codecrow_ai;"
docker compose restart web-server
```

3. **Redis connection failed**:
```bash
docker ps | grep redis
docker logs codecrow-redis
```

### Pipeline Agent Issues

**Analysis stuck in processing**:

1. **Check for stale locks**:
```sql
SELECT * FROM analysis_lock WHERE locked_at < NOW() - INTERVAL '30 minutes';
-- Remove stale locks
DELETE FROM analysis_lock WHERE locked_at < NOW() - INTERVAL '30 minutes';
```

2. **Check MCP client connectivity**:
```bash
docker exec codecrow-pipeline-agent curl http://mcp-client:8000/health
```

3. **Check RAG pipeline connectivity**:
```bash
docker exec codecrow-pipeline-agent curl http://rag-pipeline:8001/health
```

**Webhook not received**:

1. **Verify firewall allows Bitbucket IPs**:
```bash
# Check current firewall rules
ufw status
# Allow Bitbucket IP ranges
ufw allow from 104.192.136.0/21
ufw allow from 185.166.140.0/22
```

2. **Check webhook configuration in Bitbucket**:
- URL correct (https://domain.com/webhook)
- Events enabled (PR created, PR updated, Repo push)
- Token in Authorization header

3. **Test webhook manually**:
```bash
curl -X POST http://localhost:8082/api/v1/bitbucket-cloud/webhook \
  -H "Authorization: Bearer <project-token>" \
  -H "Content-Type: application/json" \
  -d @sample-webhook.json
```

### MCP Client Issues

**Service not responding**:

1. **Check logs**:
```bash
docker logs codecrow-mcp-client
```

2. **Verify Java MCP servers loaded**:
```bash
docker exec codecrow-mcp-client ls -la /app/codecrow-mcp-servers-1.0.jar
```

3. **Rebuild if JAR missing**:
```bash
./tools/production-build.sh
```

**OpenRouter API errors**:

1. **Invalid API key**:
```bash
# Check .env file
docker exec codecrow-mcp-client cat /app/.env | grep OPENROUTER_API_KEY
```

2. **Rate limiting**:
- Check OpenRouter dashboard for quota
- Reduce analysis frequency
- Use smaller models

3. **Model not available**:
```bash
# Check model name is correct
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
```

### RAG Pipeline Issues

**Indexing fails**:

1. **Qdrant connection failed**:
```bash
docker logs codecrow-qdrant
curl http://localhost:6333/collections
```

2. **OpenRouter embedding errors**:
- Check API key is valid
- Verify model supports embeddings
- Check for rate limits

3. **Out of memory**:
```yaml
# Increase memory limit
rag-pipeline:
  deploy:
    resources:
      limits:
        memory: 4G
```

**Slow indexing**:

1. **Reduce chunk size**:
```bash
# .env
CHUNK_SIZE=500
TEXT_CHUNK_SIZE=800
```

2. **Skip large files**:
```bash
MAX_FILE_SIZE_BYTES=524288  # 512KB
```

3. **Monitor Qdrant performance**:
```bash
docker stats codecrow-qdrant
```

### Frontend Issues

**Can't connect to API**:

1. **Check backend URL**:
```bash
# .env
VITE_API_URL=http://localhost:8081/api
```

2. **CORS errors**:
```java
// WebSecurityConfig.java
@Bean
public CorsConfigurationSource corsConfigurationSource() {
    CorsConfiguration config = new CorsConfiguration();
    config.setAllowedOrigins(Arrays.asList("http://localhost:5173", "http://localhost:8080"));
    config.setAllowedMethods(Arrays.asList("*"));
    config.setAllowedHeaders(Arrays.asList("*"));
    config.setAllowCredentials(true);
    // ...
}
```

3. **Clear browser cache**:
- Hard refresh (Ctrl+Shift+R)
- Clear localStorage
- Use incognito mode

**Build fails**:

```bash
# Clear cache and rebuild
rm -rf node_modules dist
npm install
npm run build
```

## Analysis Issues

### No Issues Found

**Possible causes**:

1. **Prompt not effective**: Adjust prompts in MCP client
2. **Model too lenient**: Try different model or adjust temperature
3. **RAG context missing**: Verify RAG indexing completed
4. **Files not analyzed**: Check changed files are included

### False Positives

**Solutions**:

1. **Improve prompts**: Add more context about project standards
2. **Adjust severity thresholds**: Filter low-severity issues
3. **Add exclusion patterns**: Ignore test files, generated code
4. **Fine-tune model**: Provide examples of correct code

### Analysis Timeout

**Increase timeouts**:

```properties
# application.properties
spring.mvc.async.request-timeout=600000  # 10 minutes
```

```python
# MCP client
httpx.Client(timeout=300.0)  # 5 minutes
```

**Optimize analysis**:
- Reduce files analyzed
- Limit RAG context results
- Use faster model

## Performance Issues

### Slow Response Times

**Database queries**:

1. **Enable query logging**:
```properties
spring.jpa.show-sql=true
logging.level.org.hibernate.SQL=DEBUG
```

2. **Add indexes**:
```sql
CREATE INDEX idx_branch_issue_branch_resolved ON branch_issue(branch_id, resolved);
```

3. **Optimize queries**:
```java
// Use projections instead of full entities
interface ProjectSummary {
    String getId();
    String getName();
}
```

**High CPU usage**:

```bash
# Check container stats
docker stats

# Limit CPU usage
docker compose down
# Edit docker-compose.yml to add CPU limits
docker compose up -d
```

**High memory usage**:

```bash
# Check memory usage
docker stats

# Adjust JVM heap
environment:
  JAVA_OPTS: "-Xmx1G -Xms512M"
```

### Database Growing Large

**Check table sizes**:
```sql
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

**Clean up old data**:
```sql
-- Remove old resolved issues
DELETE FROM branch_issue 
WHERE resolved = TRUE 
AND resolved_at < NOW() - INTERVAL '6 months';

-- Remove old analyses
DELETE FROM code_analysis 
WHERE completed_at < NOW() - INTERVAL '3 months';

-- Vacuum to reclaim space
VACUUM FULL;
```

**Archive strategy**:
```sql
-- Move old data to archive table
CREATE TABLE code_analysis_archive AS 
SELECT * FROM code_analysis 
WHERE completed_at < NOW() - INTERVAL '1 year';

DELETE FROM code_analysis 
WHERE id IN (SELECT id FROM code_analysis_archive);
```

## Authentication Issues

### Can't Login

**Check user exists**:
```sql
SELECT * FROM "user" WHERE email = 'user@example.com';
```

**Reset password**:
```bash
# Generate new hash (use BCrypt generator or Spring Boot app)
# Update database
UPDATE "user" SET password = '$2a$10$newhash' WHERE email = 'user@example.com';
```

**JWT token expired**:
- Tokens expire after configured time
- Login again to get new token
- Increase expiration time in config

**Token validation fails**:

1. **Check JWT secret is consistent**:
```properties
# Same secret in all instances
codecrow.security.jwtSecret=<same-value>
```

2. **Clear old sessions**:
```bash
docker exec codecrow-redis redis-cli FLUSHDB
```

### Permission Denied

**Check workspace membership**:
```sql
SELECT * FROM workspace_member 
WHERE user_id = '<user-uuid>' AND workspace_id = '<workspace-uuid>';
```

**Check project permissions**:
```sql
SELECT * FROM project_permission_assignment 
WHERE user_id = '<user-uuid>' AND project_id = '<project-uuid>';
```

**Grant access**:
- Add user to workspace as member
- Assign project permissions

## Data Issues

### Missing Data

**Database connection lost**:
- Check database is running
- Verify connection pool settings
- Check for network issues

**Transaction rollback**:
- Check application logs for exceptions
- Verify constraints not violated
- Check foreign key relationships

### Corrupted Data

**Inconsistent state**:
```sql
-- Find orphaned records
SELECT * FROM code_analysis_issue 
WHERE analysis_id NOT IN (SELECT id FROM code_analysis);

-- Clean up
DELETE FROM code_analysis_issue 
WHERE analysis_id NOT IN (SELECT id FROM code_analysis);
```

**Fix relationships**:
```sql
-- Ensure all projects belong to existing workspaces
UPDATE project 
SET workspace_id = (SELECT id FROM workspace LIMIT 1)
WHERE workspace_id NOT IN (SELECT id FROM workspace);
```

## Monitoring & Debugging

### Enable Debug Logging

**Java services**:
```properties
logging.level.org.rostilos.codecrow=DEBUG
logging.level.org.springframework.web=DEBUG
logging.level.org.hibernate.SQL=DEBUG
```

**Python services**:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Check Service Health

```bash
# All services
docker compose ps

# Specific service health
curl http://localhost:8081/actuator/health
curl http://localhost:8000/health
curl http://localhost:8001/health

# Service logs
docker compose logs -f web-server
docker compose logs -f pipeline-agent
```

### Database Inspection

```bash
# Connect to database
docker exec -it codecrow-postgres psql -U codecrow_user -d codecrow_ai

# Common queries
\dt                                    -- List tables
\d+ table_name                        -- Table structure
SELECT count(*) FROM "user";          -- Count users
SELECT * FROM analysis_lock;          -- Check locks
```

### Network Issues

**Test connectivity**:
```bash
# From host to container
curl http://localhost:8081/actuator/health

# Between containers
docker exec codecrow-pipeline-agent curl http://mcp-client:8000/health
docker exec codecrow-mcp-client curl http://rag-pipeline:8001/health
```

**DNS resolution**:
```bash
docker exec codecrow-web-application nslookup postgres
```

## Getting Help

### Collect Information

When reporting issues, include:

1. **Version information**:
```bash
git rev-parse HEAD
docker --version
docker compose version
```

2. **Service logs**:
```bash
docker compose logs > all-logs.txt
```

3. **Configuration** (redact secrets):
```bash
cat deployment/config/java-shared/application.properties
```

4. **System information**:
```bash
uname -a
docker info
```

### Support Channels

- GitHub Issues: Bug reports and feature requests
- GitHub Discussions: Questions and community support
- Documentation: Check all docs first
- Logs: Enable debug logging for detailed info

### Emergency Recovery

**Complete reset** (data loss!):
```bash
cd deployment
docker compose down -v
docker system prune -a
# Reconfigure and restart
./tools/production-build.sh
```

**Restore from backup**:
```bash
# Restore database
gunzip < backup.sql.gz | \
  docker exec -i codecrow-postgres psql -U codecrow_user -d codecrow_ai

# Restart services
docker compose restart
```

