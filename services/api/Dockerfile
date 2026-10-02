FROM golang:1.24.7-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /api .
FROM alpine:3.21.3
RUN apk add --no-cache ca-certificates wget && adduser -D -u 10001 api
COPY --from=build /api /api
USER api
EXPOSE 8080
ENTRYPOINT ["/api"]
