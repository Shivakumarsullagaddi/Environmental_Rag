# Production Dockerfile for Environmental AI Scientist on Cloud Run
FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOST=0.0.0.0 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies including Node.js and npm for Tavily MCP (npx)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    nodejs \
    npm \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create a dedicated non-root user and group
RUN groupadd -r -g 10001 appuser && \
    useradd -r -u 10001 -g appuser -m -d /home/appuser appuser

# Install pinned Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code into /app
COPY implimentation /app

# Ensure proper non-root permissions
RUN chown -R appuser:appuser /app /home/appuser

# Switch to non-root user for runtime security
USER appuser

EXPOSE 8080

# Start FastAPI server listening on 0.0.0.0:$PORT with graceful SIGTERM forwarding
CMD ["sh", "-c", "exec uvicorn server:api_app --host 0.0.0.0 --port ${PORT:-8080}"]
