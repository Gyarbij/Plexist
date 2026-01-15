# ---------- builder ----------
FROM python:3.14.2-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_PRE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .

RUN apt-get update && apt-get install -y \
    libgcc-s1 \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv \
 && /opt/venv/bin/python -m pip install --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: --no-deps -r requirements.txt

# ---------- runtime ----------
FROM gcr.io/distroless/base-debian13:nonroot AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:/usr/local/bin:$PATH"

WORKDIR /app

# Copy the Python runtime from the python image
COPY --from=builder /usr/local /usr/local

# Copy dependencies + app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /lib/*/libgcc_s.so.1 /lib/
COPY --from=builder /lib/*/libstdc++.so.6 /lib/
COPY plexist /app/plexist

USER 65532
CMD ["python", "plexist/plexist.py"]
