# Test-only MinIO server, built from the official pinned source release.
# Upstream distributes this release as source rather than a public Docker image.
FROM golang:1.24.8 AS build
ENV CGO_ENABLED=0 GOBIN=/out GOMAXPROCS=2 GOFLAGS=-p=2
RUN go install github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z

FROM debian:bookworm-slim
RUN useradd --create-home --uid 10001 minio \
    && mkdir /data && chown minio:minio /data
COPY --from=build /out/minio /usr/local/bin/minio
USER minio
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
