ARG BASE=concorde3:pulse-lean-20260915
FROM ${BASE}
COPY concorde3 /usr/local/bin/concorde3
USER root
RUN chmod 755 /usr/local/bin/concorde3
USER node
