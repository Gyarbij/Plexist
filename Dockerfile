FROM python:3.13.1-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV CONFIG_PATH=/config/config.json
ENV DATA_DIR=/data

# Install system dependencies
RUN apk add --no-cache \
    gcc \
    libffi-dev \
    build-base

# Create app user
RUN adduser -D -u 5678 plexist

# Create necessary directories
RUN mkdir -p /app /config /data \
    && chown -R plexist:plexist /app /config /data

WORKDIR /app
COPY --chown=plexist:plexist requirements.txt .

# Install Python dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=plexist:plexist . /app

USER plexist

RUN echo '{"users":[],"write_missing_as_csv":true,"add_playlist_poster":true,"add_playlist_description":true,"append_instead_of_sync":false,"seconds_to_wait":84000}' > /config/config.json.example

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import os; assert os.path.exists('/config/config.json')" || exit 1

CMD ["python", "plexist/plexist.py", "--config", "/config/config.json"]