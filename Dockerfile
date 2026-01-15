# ---------- builder ----------
FROM python:3.14.2-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_PRE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends \
      libsqlite3-0 \
      libgcc-s1 \
      libstdc++6 \
      build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv \
 && /opt/venv/bin/python -m pip install --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------- runtime ----------
FROM gcr.io/distroless/cc-debian13:nonroot AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:/usr/local/bin:$PATH" \
    LD_LIBRARY_PATH="/lib:/usr/lib:/usr/local/lib"

WORKDIR /app

COPY --from=builder /usr/local /usr/local

COPY --from=builder /opt/venv /opt/venv
COPY plexist /app/plexist

COPY --from=builder /lib/ /lib/
COPY --from=builder /usr/lib/ /usr/lib/

USER 65532
CMD ["python", "plexist/plexist.py"]
