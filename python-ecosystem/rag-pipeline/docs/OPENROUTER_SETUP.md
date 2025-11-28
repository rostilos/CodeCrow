# OpenRouter Setup Guide

This guide explains how to configure the RAG pipeline to use OpenRouter for embeddings.

## Why OpenRouter?

- **No Local GPU Required**: All embeddings computed remotely
- **Cost Effective**: Pay only for what you use
- **Multiple Models**: Access to various embedding models
- **High Availability**: Robust infrastructure
- **No PyTorch**: Significantly smaller Docker images

## Configuration

### Environment Variables

```bash
# OpenRouter API credentials
OPENROUTER_API_KEY=sk-or-v1-your-api-key-here

# Embedding model (optional, defaults to text-embedding-3-small)
OPENROUTER_MODEL=openai/text-embedding-3-small

# MongoDB connection
MONGO_URI=mongodb://mongodb:27017
MONGO_DB_NAME=codecrow_rag
```

### Supported Embedding Models

#### OpenAI Models (via OpenRouter)
```bash
# Fast and cost-effective (default)
OPENROUTER_MODEL=openai/text-embedding-3-small
# Dimensions: 1536
# Cost: ~$0.02 / 1M tokens

# Higher quality embeddings
OPENROUTER_MODEL=openai/text-embedding-3-large
# Dimensions: 3072
# Cost: ~$0.13 / 1M tokens

# Legacy model
OPENROUTER_MODEL=openai/text-embedding-ada-002
# Dimensions: 1536
```

#### Other Models
Check OpenRouter's catalog for additional embedding models.

## Docker Configuration

### docker-compose.yml

```yaml
rag-pipeline:
  build:
    context: ./codecrow-rag-pipeline
  environment:
    MONGO_URI: mongodb://mongodb:27017
    MONGO_DB_NAME: codecrow_rag
    OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
    OPENROUTER_MODEL: ${OPENROUTER_MODEL:-openai/text-embedding-3-small}
  depends_on:
    - mongodb
```

### .env File

```bash
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxx
OPENROUTER_MODEL=openai/text-embedding-3-small
```

## Getting OpenRouter API Key

1. Visit https://openrouter.ai/
2. Sign up or log in
3. Go to Keys section
4. Create new API key
5. Copy the key (starts with `sk-or-v1-`)

## Costs Estimation

### For a typical code repository:

**Small Project** (100 files, ~50KB total):
- Chunks: ~100-200
- Tokens: ~25,000
- Cost: < $0.001 (less than a penny)

**Medium Project** (1000 files, ~2MB total):
- Chunks: ~2,000
- Tokens: ~500,000
- Cost: ~$0.01 (1 cent)

**Large Project** (10,000 files, ~50MB total):
- Chunks: ~50,000
- Tokens: ~12,500,000
- Cost: ~$0.25 (25 cents)

**Query Cost**:
- Each query: 1 embedding = negligible cost
- 1000 queries: < $0.001

## MongoDB Vector Index Configuration

### Required Index

For MongoDB Atlas Vector Search:

```javascript
{
  "type": "vectorSearch",
  "fields": [{
    "type": "vector",
    "path": "embedding",
    "numDimensions": 1536,  // Use 3072 for text-embedding-3-large
    "similarity": "cosine"
  }]
}
```

### For MongoDB 7.0+ (Self-Hosted)

Enable Atlas Search or use community vector search extensions.

## Performance Tips

### Batch Processing
When indexing large repositories, the pipeline automatically batches document processing.

### Caching
Consider implementing caching for frequently accessed embeddings:
- Cache embeddings at application level
- Use Redis for query result caching

### Rate Limits
OpenRouter has generous rate limits, but for very large repositories:
- Add delays between batch operations
- Monitor API usage in OpenRouter dashboard

## Troubleshooting

### "Invalid API Key"
- Verify key starts with `sk-or-v1-`
- Check environment variable is set correctly
- Ensure no extra spaces in .env file

### "Model not found"
- Verify model name is correct
- Check OpenRouter's current model availability
- Default to `openai/text-embedding-3-small` if unsure

### "Rate limit exceeded"
- Wait a few seconds and retry
- Reduce batch size
- Check your OpenRouter account limits

### "Connection timeout"
- Increase timeout in config (default 60s)
- Check network connectivity
- Verify OpenRouter API status

## Migration from Local Embeddings

If you were using local embeddings (sentence-transformers):

1. Update requirements.txt (remove pytorch, sentence-transformers)
2. Set OPENROUTER_API_KEY environment variable
3. Rebuild Docker image
4. Re-index repositories (embeddings are incompatible)

### Benefits of Migration:
- **90% smaller Docker image** (no PyTorch)
- **Faster builds** (no heavy dependencies)
- **Better quality** embeddings (OpenAI models)
- **No GPU management**

## Security Best Practices

1. **Never commit API keys** to version control
2. Use environment variables or secrets management
3. Rotate keys periodically
4. Monitor API usage in OpenRouter dashboard
5. Set spending limits if available

## Testing Configuration

```bash
# Test API connection
curl https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"

# Test embedding generation
curl https://openrouter.ai/api/v1/embeddings \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/text-embedding-3-small",
    "input": "test string"
  }'
```

## Example Configuration File

Create `.env` in project root:

```bash
# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_MODEL=openai/text-embedding-3-small

# MongoDB
MONGO_URI=mongodb://mongodb:27017
MONGO_DB_NAME=codecrow_rag

# Optional: Chunking
CHUNK_SIZE=800
CHUNK_OVERLAP=200
TEXT_CHUNK_SIZE=1000
TEXT_CHUNK_OVERLAP=200

# Optional: Retrieval
RETRIEVAL_TOP_K=10
SIMILARITY_THRESHOLD=0.7
```

## Monitoring

Monitor your OpenRouter usage:
1. Login to https://openrouter.ai/
2. Check Usage section
3. View API calls, costs, errors
4. Set up spending alerts

## Support

- OpenRouter Docs: https://openrouter.ai/docs
- OpenRouter Discord: https://discord.gg/openrouter
- LlamaIndex Docs: https://docs.llamaindex.ai/

