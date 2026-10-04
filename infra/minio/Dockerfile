# Upstream stopped publishing public images/binaries; build pinned source.
FROM golang:1.24.8-alpine AS server-build
RUN apk add --no-cache git
RUN git clone --depth 1 --branch RELEASE.2025-10-15T17-29-55Z https://github.com/minio/minio.git /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -o /out/minio .

FROM golang:1.24.8-alpine AS client-build
RUN apk add --no-cache git
RUN git clone --depth 1 --branch RELEASE.2025-04-16T18-13-26Z https://github.com/minio/mc.git /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -trimpath -o /out/mc .

FROM alpine:3.21.3 AS minio
RUN apk add --no-cache ca-certificates curl && adduser -D -u 10001 minio && mkdir /data && chown minio:minio /data
COPY --from=server-build /out/minio /usr/local/bin/minio
COPY --from=server-build /src/LICENSE /usr/share/licenses/minio/LICENSE
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]

FROM alpine:3.21.3 AS mc
RUN apk add --no-cache ca-certificates && adduser -D -u 10001 minio
COPY --from=client-build /out/mc /usr/local/bin/mc
COPY --from=client-build /src/LICENSE /usr/share/licenses/mc/LICENSE
USER minio
ENTRYPOINT ["mc"]
