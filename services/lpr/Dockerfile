FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ---- 1. System libraries (OpenCV runtime) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates libglib2.0-0 libgl1 libgomp1 \
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

# ---- 4. Code (models and data are mounted as volumes, not baked in) ----
COPY *.py run_pipeline.sh ./
RUN chmod +x run_pipeline.sh && mkdir -p models easyocr_models data output

ENV EASYOCR_MODELS=/app/easyocr_models

CMD ["./run_pipeline.sh"]
