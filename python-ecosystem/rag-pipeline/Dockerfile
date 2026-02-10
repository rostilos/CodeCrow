FROM python:3.11-slim


WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY main.py .

RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# Create cache directories with proper permissions
RUN mkdir -p /tmp/.tiktoken_cache /tmp/.transformers_cache /tmp/.huggingface /tmp/.llama_index && \
    chmod -R 777 /tmp && \
    chown -R appuser:appgroup /app

USER appuser

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV HOME=/tmp
ENV TIKTOKEN_CACHE_DIR=/tmp/.tiktoken_cache
ENV TRANSFORMERS_CACHE=/tmp/.transformers_cache
ENV HF_HOME=/tmp/.huggingface
ENV LLAMA_INDEX_CACHE_DIR=/tmp/.llama_index
# Allow concurrent indexing by running multiple Uvicorn workers
ENV UVICORN_WORKERS=4

# CPU Threading Optimization (can be overridden via .env)
# These control parallelism for numerical operations
ENV OMP_NUM_THREADS=4
ENV MKL_NUM_THREADS=4
ENV OPENBLAS_NUM_THREADS=4
ENV NUMEXPR_NUM_THREADS=4

EXPOSE 8001

CMD ["python", "main.py"]

