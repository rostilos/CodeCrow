FROM python:3.11-slim-bullseye AS builder

# Set work directory
WORKDIR /app

# --- Builder Stage 1: Install Dependencies ---
COPY requirements.txt .

# Install build dependencies required by some Python packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libc-dev \
        # Add 'make' or 'cmake' if needed for native dependencies, but not strictly necessary here.
        && apt-get clean && \
        rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt


# --- Builder Stage 2: Copy and Install Application Modules ---
# Copy application source code and the JAR
COPY main.py .
COPY api ./api/
COPY server ./server/
COPY model ./model/
COPY service ./service/
COPY utils ./utils/
COPY llm ./llm/
# Copy the JAR files (required by the Python service)
COPY codecrow-vcs-mcp-1.0.jar ./codecrow-vcs-mcp-1.0.jar
# Platform MCP JAR - if it exists (optional)
COPY codecrow-platform-mcp-1.0.jar* ./

# --- Production Stage ---
FROM python:3.11-slim-bullseye

# CRITICAL FIX: Install Java Runtime Environment (JRE)
# The 'java' command will be available after this.
RUN apt-get update && \
    # Install openjdk-17-jre-headless for a minimal Java 17 runtime
    apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user and set permissions
RUN groupadd -r appuser && useradd -r -g appuser appuser
RUN mkdir -p /app && chown -R appuser:appuser /app

WORKDIR /app

# Copy installed dependencies directly from the builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages

# Copy application code from the builder stage
COPY --chown=appuser:appuser --from=builder /app/main.py ./
COPY --chown=appuser:appuser --from=builder /app/api ./api/
COPY --chown=appuser:appuser --from=builder /app/server ./server/
COPY --chown=appuser:appuser --from=builder /app/model ./model/
COPY --chown=appuser:appuser --from=builder /app/service ./service/
COPY --chown=appuser:appuser --from=builder /app/utils ./utils/
COPY --chown=appuser:appuser --from=builder /app/llm ./llm/
# Copy the JAR files
COPY --chown=appuser:appuser --from=builder /app/codecrow-vcs-mcp-1.0.jar ./codecrow-vcs-mcp-1.0.jar
# Copy Platform MCP JAR if present
COPY --chown=appuser:appuser --from=builder /app/codecrow-platform-mcp-1.0.jar* ./

# Set PYTHONPATH environment variable to include the current working directory.
ENV PYTHONPATH=/app

# Switch to non-root user
USER appuser

# Expose port (default for FastAPI/Uvicorn)
EXPOSE 8000

# Define entrypoint and command
ENTRYPOINT ["/usr/local/bin/python"]

# Default command to run the HTTP server
CMD ["main.py"]