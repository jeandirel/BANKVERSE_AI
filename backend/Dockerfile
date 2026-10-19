# syntax=docker/dockerfile:1.7
# BANKVERSE AI — backend image (API by default; the same image runs pipelines/training as an ECS task
# when built with --build-arg EXTRAS=train,spark and a Java runtime, see target "worker").

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# OCR engine (French + English) and the shared libraries required by OpenCV / PyMuPDF.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng libgl1 libglib2.0-0 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 bankverse && useradd --system --uid 10001 --gid bankverse --home /app bankverse
WORKDIR /app

FROM base AS deps
ARG EXTRAS=""
COPY pyproject.toml README.md ./
# CPU-only PyTorch wheels (no CUDA in the image; GPU training is an explicit, separately budgeted option).
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.4"
COPY src ./src
RUN if [ -n "$EXTRAS" ]; then /opt/venv/bin/pip install ".[${EXTRAS}]"; else /opt/venv/bin/pip install .; fi

FROM base AS api
COPY --from=deps /opt/venv /opt/venv
COPY --chown=bankverse:bankverse alembic.ini ./
COPY --chown=bankverse:bankverse src ./src
COPY --chown=bankverse:bankverse scripts ./scripts
ENV PATH="/opt/venv/bin:$PATH" BANKVERSE_ENV=staging
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"
CMD ["uvicorn", "bankverse.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "2"]

FROM api AS worker
USER root
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless && rm -rf /var/lib/apt/lists/*
USER 10001:10001
ENTRYPOINT ["bankverse"]
CMD ["--help"]
