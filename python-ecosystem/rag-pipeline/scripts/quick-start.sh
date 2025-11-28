#!/bin/bash

set -e

# Change to project root
cd "$(dirname "$0")/.."

echo "========================================="
echo "CodeCrow RAG Pipeline - Quick Start"
echo "========================================="
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    echo ""
    echo "Please create .env file with:"
    echo "  OPENROUTER_API_KEY=your-key-here"
    echo ""
    echo "You can copy from .env.sample:"
    echo "  cp .env.sample .env"
    echo "  nano .env"
    exit 1
fi

# Check if OPENROUTER_API_KEY is set
source .env
if [ -z "$OPENROUTER_API_KEY" ] || [ "$OPENROUTER_API_KEY" = "your_openrouter_api_key_here" ]; then
    echo "❌ OPENROUTER_API_KEY not configured in .env"
    echo ""
    echo "Please update .env with your OpenRouter API key"
    exit 1
fi

echo "✅ Configuration found"
echo ""

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running"
    echo ""
    echo "Please start Docker and try again"
    exit 1
fi

echo "✅ Docker is running"
echo ""

# Check if running from root directory
if [ ! -f "../docker-compose.yml" ]; then
    echo "❌ docker-compose.yml not found"
    echo ""
    echo "Please run this script from the codecrow-rag-pipeline directory"
    echo "Or use the root-level start-rag-pipeline.sh script"
    exit 1
fi

echo "Starting RAG Pipeline services..."
echo ""

# Start MongoDB
echo "Starting MongoDB..."
cd .. && docker-compose up -d mongodb && cd codecrow-rag-pipeline

# Wait for MongoDB to be ready
echo "Waiting for MongoDB to be ready..."
sleep 10

# Check MongoDB health
if docker exec codecrow-mongodb mongosh --eval "db.adminCommand('ping')" > /dev/null 2>&1; then
    echo "✅ MongoDB is ready"
else
    echo "⚠️  MongoDB may still be starting up"
fi

echo ""

# Build and start RAG pipeline
echo "Building and starting RAG Pipeline..."
cd .. && docker-compose up -d --build rag-pipeline && cd codecrow-rag-pipeline

# Wait for service to start
echo "Waiting for RAG Pipeline to start..."
sleep 5

# Check health
echo ""
echo "Checking RAG Pipeline health..."
for i in {1..10}; do
    if curl -s http://localhost:8001/health > /dev/null 2>&1; then
        echo "✅ RAG Pipeline is healthy"
        break
    fi

    if [ $i -eq 10 ]; then
        echo "⚠️  RAG Pipeline may still be starting"
        echo "   Check logs: docker logs codecrow-rag-pipeline"
    else
        sleep 2
    fi
done

echo ""
echo "========================================="
echo "Services Started Successfully!"
echo "========================================="
echo ""
echo "📊 Service Status:"
cd .. && docker-compose ps mongodb rag-pipeline && cd codecrow-rag-pipeline
echo ""

echo "🔗 Endpoints:"
echo "  - RAG API:      http://localhost:8001"
echo "  - Health Check: http://localhost:8001/health"
echo "  - API Docs:     http://localhost:8001/docs"
echo "  - MongoDB:      mongodb://localhost:27017"
echo ""

echo "📝 Quick Test:"
echo "  curl http://localhost:8001/health"
echo ""

echo "📚 Documentation:"
echo "  - README.md              - Module overview"
echo "  - INTEGRATION_GUIDE.md   - How to integrate"
echo "  - DEPLOYMENT.md          - Deployment guide"
echo "  - IMPLEMENTATION_SUMMARY.md - What was built"
echo ""

echo "🛠️  Useful Commands:"
echo "  - View logs:     docker logs -f codecrow-rag-pipeline"
echo "  - Stop services: docker-compose stop mongodb rag-pipeline"
echo "  - Restart:       docker-compose restart rag-pipeline"
echo ""

echo "✅ Setup complete! RAG Pipeline is ready to use."

