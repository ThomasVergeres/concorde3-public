FROM concorde3:luna6-max-20260925
USER root
RUN apt-get update && apt-get install -y --no-install-recommends chromium fonts-dejavu fonts-liberation && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /opt/ui && cd /opt/ui && npm install --ignore-scripts --no-audit --no-fund playwright-core@1.56.1
COPY smoke.cjs /opt/ui/smoke.cjs
RUN chmod 644 /opt/ui/smoke.cjs
ENV NODE_PATH=/opt/ui/node_modules
ENV XDG_CONFIG_HOME=/tmp/ui-config XDG_CACHE_HOME=/tmp/ui-cache
USER node
