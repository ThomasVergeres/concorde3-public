FROM concorde3:luna6-max-20260925
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-15 postgresql-client-15 libpq-dev python3-psycopg2 \
    python3-pytest python3-sqlalchemy python3-venv build-essential curl ca-certificates \
    && apt-get clean
ARG ATLAS_SHA256=10d7913e3dce43ab99b8d71534a4cbadaf11a16dc293adf3b91d10e83a0ac70b
RUN curl -fsSL https://release.ariga.io/atlas/atlas-community-linux-amd64-v1.3.0 -o /usr/local/bin/atlas \
    && echo "${ATLAS_SHA256}  /usr/local/bin/atlas" | sha256sum -c - \
    && chmod 755 /usr/local/bin/atlas
COPY smoke.py /opt/migration/smoke.py
RUN chmod 644 /opt/migration/smoke.py
ENV PATH=/usr/lib/postgresql/15/bin:$PATH
ENV ATLAS_NO_UPDATE_NOTIFIER=true
USER node
RUN python3 -c "import psycopg2, sqlalchemy, pytest; from pathlib import Path; compile(Path('/opt/migration/smoke.py').read_text(), 'smoke.py', 'exec')" && atlas version
