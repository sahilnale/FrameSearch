ARG PROCESSOR_IMAGE=framesearch-processor:storage-checkpoint
FROM ${PROCESSOR_IMAGE}
USER root
RUN uv sync --frozen
USER processor
