# ---------- builder ----------
FROM python:3.14.2-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_PRE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .

RUN python -m venv /opt/venv \
 && /opt/venv/bin/python -m pip install --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: --no-deps -r requirements.txt

# ---------- runtime ----------
FROM gcr.io/distroless/cc-debian13:nonroot AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:/usr/local/bin:$PATH"

WORKDIR /app
COPY --from=builder /usr/local /usr/local
COPY --from=builder /opt/venv /opt/venv
COPY plexist /app/plexist

USER 65532
CMD ["python", "plexist/plexist.py"]
