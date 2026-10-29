# --- BACKEND MICROSERVICE (API + AGENT) ---
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (needed for some PDF parsing libraries)
RUN apt-get update && apt-get install -y \
    build-essential \
    libmagic-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Bake the guardrails embedding model (FastEmbed, ~90 MB) into the image,
# otherwise every cold start downloads it before the first request
ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN python -c "from app.guardrails.rails import initialize_rails; initialize_rails()"

# Set Environment Variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Cloud Run uses the PORT env var
EXPOSE 8080

# Start the FastAPI application
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
