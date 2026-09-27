# Reuse a qualified subscription runtime while comparing only the C3 binary.
ARG BASE_IMAGE=concorde3:context-bounded
FROM golang:1.24-bookworm AS build
WORKDIR /src
COPY go.mod ./
COPY core ./core
COPY cmd ./cmd
ARG VCS_REF=unversioned
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-X github.com/ThomasVergeres/concorde3-public/core.BuildStamp=${VCS_REF}" -o /concorde3 ./cmd/concorde3
FROM ${BASE_IMAGE}
ARG VCS_REF=unversioned
LABEL org.opencontainers.image.revision=${VCS_REF}
COPY --from=build /concorde3 /usr/local/bin/concorde3
