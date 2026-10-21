# syntax=docker/dockerfile:1.6
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Set to true when building behind NetFree (installs its CA certificates)
ARG NETFREE=false

WORKDIR /app

# ---- 1. System libraries (OpenCV runtime) ----
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl libglib2.0-0 libgl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ---- 2. Optional NetFree CA bundle ----
RUN if [ "$NETFREE" = "true" ]; then \
      curl -kfsSL https://netfree.link/dl/unix-ca.sh -o /tmp/netfree-ca.sh \
      && sh /tmp/netfree-ca.sh \
      && rm -f /tmp/netfree-ca.sh \
      && if [ -s /etc/ca-bundle.crt ]; then cp /etc/ca-bundle.crt /etc/ssl/certs/ca-certificates.crt; fi ; \
    fi

ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
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
