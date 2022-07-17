FROM python:3.11-rc-alpine

ENV PYTHONUNBUFFERED 1

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . .
WORKDIR /app

CMD ["python", "./plexist/sync.py"]

# docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 -t gyarbij/plexist:<tag> --push .