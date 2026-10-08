FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.6.3
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 processor \
    && mkdir -p /cache/openclip && chown -R processor:processor /cache
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1 \
    MODEL_CACHE_DIR=/cache/openclip HF_HOME=/cache/huggingface HF_HUB_DISABLE_TELEMETRY=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY framesearch_processor ./framesearch_processor
USER processor
EXPOSE 8000
CMD [".venv/bin/uvicorn", "framesearch_processor.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
