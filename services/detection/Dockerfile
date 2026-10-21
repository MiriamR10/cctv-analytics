FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ---- 1. System libraries (OpenCV / video) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ---- 2. Organisation CA certificates (any *.crt in ./certs, see README) ----
COPY --from=certs . /usr/local/share/ca-certificates/org/
RUN update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# ---- 3. Python dependencies (CPU-only PyTorch keeps the image small) ----
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# ---- 4. Code (models are mounted as a volume, not baked in) ----
COPY app ./app
RUN mkdir -p /app/data/uploads /app/data/results /models

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
