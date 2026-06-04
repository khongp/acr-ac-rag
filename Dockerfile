# Use lightweight Python 3.11 slim image
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies for ChromaDB C-extensions and curl for health check
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set default production environment variables
ENV PORT=8080
ENV EMBEDDING_MODE=gemini

# Create a non-root user and change ownership of application files
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser:appuser /app
USER appuser

# Health check configuration
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:$PORT/health || exit 1

# Run the app with Uvicorn, binding to the port set by Cloud Run ($PORT)
CMD exec uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1 --timeout-keep-alive 30

