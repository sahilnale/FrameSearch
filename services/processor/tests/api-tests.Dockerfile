ARG API_IMAGE=framesearch-api:queue-checkpoint
ARG PROCESSOR_TEST_IMAGE=framesearch-processor:kafka-tests
FROM ${API_IMAGE} AS api
FROM ${PROCESSOR_TEST_IMAGE}
# The actual Go executable built by the unchanged Developer 1 Dockerfile.
COPY --from=api /api /usr/local/bin/framesearch-api
