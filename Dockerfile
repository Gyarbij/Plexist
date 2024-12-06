FROM python:3.13.1-alpine

# Set Python environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV CONFIG_PATH=/config/config.json
ENV DATA_DIR=/data

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN adduser -u 5678 --disabled-password --gecos "" plexist

# Create necessary directories
RUN mkdir -p /app /config /data \
    && chown -R plexist:plexist /app /config /data

# Set working directory and copy requirements
WORKDIR /app
COPY --chown=plexist:plexist requirements.txt .

# Install Python dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=plexist:plexist . /app

# Switch to non-root user
USER plexist

# Create default config if none exists
RUN echo '{"users":[],"write_missing_as_csv":true,"add_playlist_poster":true,"add_playlist_description":true,"append_instead_of_sync":false,"seconds_to_wait":84000}' > /config/config.json.example

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import os; assert os.path.exists('${CONFIG_PATH}')" || exit 1

# Command with config path argument
CMD ["python", "plexist/plexist.py", "--config", "${CONFIG_PATH}"]

# Build command for reference:
# docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 -t gyarbij/plexist:<tag> --push .