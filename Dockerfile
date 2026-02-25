FROM --platform=$BUILDPLATFORM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# PaddleOCR + PaddlePaddle CPU wheels are multi-arch (amd64/arm64) for Python 3.11.
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir paddlepaddle && \
    pip install --no-cache-dir -r requirements.txt

FROM --platform=$TARGETPLATFORM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local /usr/local
COPY . .

CMD ["python", "main.py"]
