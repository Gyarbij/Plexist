FROM python:3.13.1-alpine

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV CONFIG_PATH=/config/config.json
ENV DATA_DIR=/data
ENV DB_PATH=/data/plexist.db

# Install system dependencies
RUN apk add --no-cache \
    gcc \
    libffi-dev \
    build-base

# Create app user
RUN adduser -D -u 5678 plexist

# Create necessary directories and set permissions
RUN mkdir -p /app /config /data \
    && chown -R plexist:plexist /app /config /data \
    && chmod 755 /data

WORKDIR /app
COPY --chown=plexist:plexist requirements.txt .

# Install Python dependencies
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=plexist:plexist . /app

USER plexist

# Create example configs
RUN echo '{"users":[],"write_missing_as_csv":true,"add_playlist_poster":true,"add_playlist_description":true,"append_instead_of_sync":false,"seconds_to_wait":84000}' > /config/config.json.example && \
    echo 'users: []\nwrite_missing_as_csv: true\nadd_playlist_poster: true\nadd_playlist_description: true\nappend_instead_of_sync: false\nseconds_to_wait: 84000' > /config/config.yaml.example

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import os; assert any(os.path.exists(f'/config/config.{ext}') for ext in ['json', 'yaml', 'yml'])" || exit 1

CMD ["python", "plexist/plexist.py", "--config", "/config/config.yaml"]