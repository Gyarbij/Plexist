FROM python:3.14.2 AS builder

ENV PYTHONDONTWRITEBYTECODE=1

ENV PYTHONUNBUFFERED=1

# Allow pre-release wheels for Python 3.14
ENV PIP_PRE=1

RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt --prefix=/install

FROM python:3.14.2-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY --from=builder /install /usr/local

WORKDIR /app
COPY plexist /app/plexist
COPY example.env /app/example.env

RUN adduser -u 5678 --disabled-password --gecos "" plexist && chown -R plexist /app
USER plexist

CMD ["python", "plexist/plexist.py"]

# docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 -t gyarbij/plexist:<tag> --push .
