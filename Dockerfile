FROM python:3.14.2-slim

ENV PYTHONDONTWRITEBYTECODE=1

ENV PYTHONUNBUFFERED=1

# Allow PyO3-based deps (pydantic-core) to build on Python 3.14
ENV PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
ENV PIP_PRE=1

RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

WORKDIR /app
COPY . /app

RUN adduser -u 5678 --disabled-password --gecos "" plexist && chown -R plexist /app
USER plexist

CMD ["python", "plexist/plexist.py"]

# docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 -t gyarbij/plexist:<tag> --push .
