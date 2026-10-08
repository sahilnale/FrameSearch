FROM framesearch-processor:storage-checkpoint
USER root
RUN uv sync --frozen
USER processor
