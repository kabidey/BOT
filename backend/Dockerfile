# Phase 28 — Production image for FastAPI backend (Mackertich ONE)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps required by some Python packages (numpy/torch/lxml/etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
        libxml2-dev \
        libxslt1-dev \
        libffi-dev \
        libssl-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching
# emergentintegrations lives on Emergent's CloudFront PyPI index (not pypi.org),
# so we need an extra-index-url. Both indices are queried; standard packages
# still come from PyPI.
ARG EMERGENT_PIP_INDEX="https://d33sy5i8bnduwe.cloudfront.net/simple/"
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
 && pip install --extra-index-url "${EMERGENT_PIP_INDEX}" -r /app/requirements.txt

# Copy backend source (Dockerfile lives in backend/, so context is backend/)
COPY . /app/

# Pre-download local sentence-transformers model? Skip: prod uses Hub AI.
# Keep image flexible — the model lazy-loads from HF only if Hub AI fails.

EXPOSE 8001

# Production uvicorn — 1 worker (Mongo connections + asyncio orchestration
# are not CPU-bound; we let uvicorn handle concurrency via asyncio).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--log-level", "info"]
