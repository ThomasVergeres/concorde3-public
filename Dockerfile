FROM golang:1.24-bookworm AS build
WORKDIR /src
COPY go.mod ./
COPY core ./core
COPY cmd ./cmd
ARG VCS_REF=unversioned
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-X github.com/ThomasVergeres/concorde3-public/core.BuildStamp=${VCS_REF}" -o /concorde3 ./cmd/concorde3

FROM node:22-bookworm-slim
ARG VCS_REF=unversioned
LABEL org.opencontainers.image.revision=${VCS_REF}
ARG CODEX_VERSION=0.156.0
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git python3 \
    && npm install -g @openai/codex@${CODEX_VERSION} \
    && apt-get clean
COPY --from=build /concorde3 /usr/local/bin/concorde3
RUN mkdir -p /instance && chown node:node /instance
USER node
WORKDIR /instance
ENTRYPOINT ["concorde3"]
