# RAG Pipeline Deployment Guide

## Quick Start

### Prerequisites

1. **MongoDB** (local or MongoDB Atlas)
2. **OpenAI API Key** with access to embeddings API
3. **Python 3.11+**
4. **Docker** (for containerized deployment)

### Local Development Setup

1. **Navigate to RAG module**:
```bash
cd codecrow-rag-pipeline
```

2. **Run setup script**:
```bash
./setup.sh
```

3. **Configure environment**:
```bash
# Edit .env file
cp .env.sample .env
nano .env
```

Update with your credentials:
```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=codecrow_rag
OPENAI_API_KEY=sk-your-actual-key-here
OPENAI_MODEL=text-embedding-3-small
```

4. **Activate virtual environment**:
```bash
source .venv/bin/activate
```

5. **Start the service**:
```bash
python api.py
```

The API will be available at `http://localhost:8001`

### Docker Deployment

From the root of the project:

1. **Set OpenAI API key**:
```bash
# Create or update .env in root
echo "OPENAI_API_KEY=sk-your-actual-key-here" >> .env
```

2. **Build and start services**:
```bash
docker-compose up -d mongodb rag-pipeline
```

3. **Verify services are running**:
```bash
docker ps | grep -E 'mongodb|rag-pipeline'
```

4. **Check logs**:
```bash
docker logs codecrow-rag-pipeline
docker logs codecrow-mongodb
```

5. **Test health endpoint**:
```bash
curl http://localhost:8001/health
```

### Full Stack Deployment

Start all services including RAG:

```bash
docker-compose up -d
```

This will start:
- PostgreSQL (port 5432)
- Redis (port 6379)
- MongoDB (port 27017)
- RAG Pipeline (port 8001)
- Pipeline Agent (port 8082)
- MCP Client (port 8000)
- Web Server (port 8081)
- Web Frontend (port 8080)

## MongoDB Atlas Setup (Production)

### 1. Create MongoDB Atlas Cluster

1. Go to [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
2. Create a new cluster (M10 or higher recommended for production)
3. Configure network access (allow your server IPs)
4. Create database user with read/write permissions

### 2. Create Vector Search Index

1. Navigate to your cluster → Browse Collections
2. Select database `codecrow_rag`
3. For each namespace collection ending with `_vectors`:
   - Click "Create Index"
   - Choose "Atlas Vector Search"
   - Use this configuration:

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1536,
      "similarity": "cosine"
    },
    {
      "type": "filter",
      "path": "metadata"
    }
  ]
}
```

### 3. Update Connection String

Update `.env`:
```env
MONGO_URI=mongodb+srv://username:password@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
```

## OpenRouter API Configuration

### Get API Key

1. Go to [OpenRouter](https://openrouter.ai/)
2. Sign up or log in
3. Navigate to API Keys
4. Create new secret key
5. Copy and save it (starts with `sk-or-v1-`)

### Choose Embedding Model

Available models (via OpenRouter):
- `openai/text-embedding-3-small` - 1536 dimensions, $0.02/1M tokens (recommended)
- `openai/text-embedding-3-large` - 3072 dimensions, $0.13/1M tokens
- `openai/text-embedding-ada-002` - 1536 dimensions, $0.10/1M tokens (legacy)

Update in `.env`:
```env
OPENROUTER_MODEL=openai/text-embedding-3-small
```

### Cost Estimation

For a typical codebase:
- 1000 files × 2KB average = 2MB
- ~500K tokens
- Cost: ~$0.01 for initial indexing
- Updates: ~$0.001 per PR with 10 files

## Production Checklist

### Security

- [ ] Use strong MongoDB passwords
- [ ] Enable MongoDB authentication
- [ ] Restrict MongoDB network access
- [ ] Secure OpenAI API key (use secrets manager)
- [ ] Enable HTTPS for API
- [ ] Implement rate limiting
- [ ] Add authentication to RAG API endpoints

### Performance

- [ ] Use MongoDB Atlas M10+ tier
- [ ] Create vector search indices
- [ ] Configure appropriate chunk sizes
- [ ] Set up connection pooling
- [ ] Enable query caching
- [ ] Monitor API response times

### Monitoring

- [ ] Set up health check monitoring
- [ ] Configure log aggregation
- [ ] Track indexing performance
- [ ] Monitor MongoDB storage usage
- [ ] Set up alerts for failures
- [ ] Track OpenAI API usage

### Backup

- [ ] Enable MongoDB automatic backups
- [ ] Test restore procedures
- [ ] Document recovery process
- [ ] Schedule regular backups

## Environment Variables Reference

### Required

```env
MONGO_URI=mongodb://localhost:27017
OPENAI_API_KEY=sk-...
```

### Optional

```env
MONGO_DB_NAME=codecrow_rag              # Default: codecrow_rag
OPENAI_MODEL=text-embedding-3-small     # Default: text-embedding-3-small
CHUNK_SIZE=800                          # Default: 800
CHUNK_OVERLAP=200                       # Default: 200
TEXT_CHUNK_SIZE=1000                    # Default: 1000
TEXT_CHUNK_OVERLAP=200                  # Default: 200
MAX_FILE_SIZE_BYTES=1048576             # Default: 1MB
RETRIEVAL_TOP_K=10                      # Default: 10
SIMILARITY_THRESHOLD=0.7                # Default: 0.7
```

## Troubleshooting

### Service won't start

**Error**: `ModuleNotFoundError: No module named 'llama_index'`

**Solution**: Install dependencies
```bash
pip install -r requirements.txt
```

**Error**: `pymongo.errors.ServerSelectionTimeoutError`

**Solution**: Check MongoDB connection
```bash
# Test MongoDB connection
mongosh mongodb://localhost:27017
```

### Indexing fails

**Error**: `OpenRouter API key not found`

**Solution**: Set API key
```bash
export OPENROUTER_API_KEY=sk-or-v1-...
# Or update .env file
```

**Error**: `File too large`

**Solution**: Increase max file size in `.env`
```env
MAX_FILE_SIZE_BYTES=5242880  # 5MB
```

### Poor search results

**Issue**: Irrelevant results returned

**Solutions**:
1. Increase top_k value
2. Adjust chunk size
3. Verify vector index is created
4. Check embedding model

### High latency

**Issue**: Queries taking too long

**Solutions**:
1. Upgrade MongoDB Atlas tier
2. Create vector search index
3. Reduce top_k value
4. Enable caching
5. Use smaller embedding model

### Storage issues

**Issue**: MongoDB running out of space

**Solutions**:
1. Delete old indices: `DELETE /index/{workspace}/{project}/{branch}`
2. Increase excluded patterns
3. Reduce chunk overlap
4. Archive old branches

## Monitoring Commands

### Check service status
```bash
docker-compose ps
```

### View logs
```bash
docker logs -f codecrow-rag-pipeline
```

### Check MongoDB stats
```bash
docker exec -it codecrow-mongodb mongosh
> use codecrow_rag
> db.stats()
> db.getCollectionNames()
```

### Test API
```bash
# Health check
curl http://localhost:8001/health

# List indices
curl http://localhost:8001/index/list

# Get stats
curl http://localhost:8001/index/stats/workspace/project/branch
```

### Monitor resource usage
```bash
docker stats codecrow-rag-pipeline codecrow-mongodb
```

## Scaling

### Horizontal Scaling

For high load, run multiple RAG API instances:

```yaml
rag-pipeline:
  # ... existing config ...
  deploy:
    replicas: 3
```

Add load balancer (nginx):

```nginx
upstream rag_backend {
    server rag-pipeline-1:8001;
    server rag-pipeline-2:8001;
    server rag-pipeline-3:8001;
}

server {
    listen 8001;
    location / {
        proxy_pass http://rag_backend;
    }
}
```

### Vertical Scaling

Increase container resources:

```yaml
rag-pipeline:
  # ... existing config ...
  deploy:
    resources:
      limits:
        cpus: '2.0'
        memory: 4G
      reservations:
        cpus: '1.0'
        memory: 2G
```

### Database Scaling

MongoDB Atlas:
- M10: 2GB RAM, 10GB storage - Small teams
- M20: 4GB RAM, 20GB storage - Medium teams  
- M30: 8GB RAM, 40GB storage - Large teams
- M40+: Enterprise scale

## Backup and Recovery

### Backup MongoDB

```bash
# Create backup
docker exec codecrow-mongodb mongodump --out=/backup

# Copy to host
docker cp codecrow-mongodb:/backup ./mongodb-backup-$(date +%Y%m%d)
```

### Restore MongoDB

```bash
# Copy backup to container
docker cp ./mongodb-backup-20241121 codecrow-mongodb:/restore

# Restore
docker exec codecrow-mongodb mongorestore /restore
```

### Backup Indices Metadata

```bash
# Export index list
curl http://localhost:8001/index/list > indices-backup.json
```

## Maintenance

### Clean old indices
```bash
# Delete branch index after merge
curl -X DELETE http://localhost:8001/index/workspace/project/old-branch
```

### Rebuild index
```bash
# Delete and recreate
curl -X DELETE http://localhost:8001/index/workspace/project/branch
curl -X POST http://localhost:8001/index/repository -d '{...}'
```

### Update configuration
```bash
# Edit .env
nano .env

# Restart service
docker-compose restart rag-pipeline
```

## Support

For issues and questions:
1. Check logs: `docker logs codecrow-rag-pipeline`
2. Verify configuration: `.env` file
3. Test MongoDB connection
4. Verify OpenAI API key
5. Review INTEGRATION_GUIDE.md

## Next Steps

1. ✅ Deploy MongoDB
2. ✅ Configure OpenAI API
3. ✅ Start RAG service
4. ⬜ Integrate with pipeline-agent
5. ⬜ Test with sample repository
6. ⬜ Monitor performance
7. ⬜ Optimize configuration
8. ⬜ Set up production monitoring

