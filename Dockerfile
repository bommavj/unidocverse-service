# ---------------- Base image ----------------
FROM python:3.11-slim

# ---------------- WORKDIR ----------------
WORKDIR /app

# ---------------- ENV ----------------
ENV POSTGRES_DB=unidocverse_db
ENV POSTGRES_USER=unidocverse
ENV POSTGRES_PASSWORD=unidocverse
ENV PGDATA=/var/lib/postgresql/data
ENV FLYWAY_VERSION=10.16.0

# ---------------- SYSTEM DEPENDENCIES ----------------
RUN apt-get update && apt-get install -y \
    postgresql \
    postgresql-client \
    postgresql-server-dev-all \
    build-essential \
    git \
    curl \
    unzip \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# ---------------- INSTALL PYTHON DEPENDENCIES ----------------
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    sqlalchemy \
    psycopg2-binary \
    pgvector \
    python-dotenv \
    ollama \
    sentence-transformers \
    torch \
    pdfplumber \
    python-docx \
    openpyxl \
    Pillow \
    httpx \
    requests \
    numpy \
    pandas

# ---------------- INSTALL PGVECTOR ----------------
RUN git clone https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && make \
    && make install \
    && cd / \
    && rm -rf /tmp/pgvector

# ---------------- INSTALL FLYWAY ----------------
RUN curl -L https://repo1.maven.org/maven2/org/flywaydb/flyway-commandline/$FLYWAY_VERSION/flyway-commandline-$FLYWAY_VERSION-linux-x64.tar.gz \
    | tar xz -C /usr/local/ \
    && ln -s /usr/local/flyway-$FLYWAY_VERSION/flyway /usr/local/bin/flyway

# ---------------- COPY APP & MIGRATIONS ----------------
COPY app/ ./app/
COPY migrations/ ./migrations/

# ---------------- EXPOSE PORTS ----------------
EXPOSE 5432 8000

# ---------------- ENTRYPOINT SCRIPT ----------------
# This script starts Postgres, waits for it, runs Flyway, and starts FastAPI
RUN printf '#!/usr/bin/env bash\n\
set -euo pipefail\n\
echo "🗄️ Starting PostgreSQL..."\n\
mkdir -p "$PGDATA" && chown -R postgres:postgres "$PGDATA"\n\
service postgresql start\n\
echo "⏳ Waiting for Postgres to be ready..."\n\
until pg_isready -h localhost -p 5432 -U $POSTGRES_USER; do sleep 1; done\n\
echo "✅ Postgres ready"\n\
echo "🧩 Creating DB and enabling pgvector..."\n\
su - postgres -c "psql -c \"CREATE DATABASE IF NOT EXISTS $POSTGRES_DB;\" || true"\n\
su - postgres -c "psql -d $POSTGRES_DB -c \"CREATE EXTENSION IF NOT EXISTS vector;\" || true"\n\
echo "🛠 Running Flyway migrations..."\n\
flyway -url=jdbc:postgresql://localhost:5432/$POSTGRES_DB -user=$POSTGRES_USER -password=$POSTGRES_PASSWORD -locations=filesystem:./migrations migrate\n\
echo "🚀 Starting FastAPI..."\n\
uvicorn app.main:app --host 0.0.0.0 --port 8000\n' > /entrypoint.sh

RUN chmod +x /entrypoint.sh

# ---------------- ENTRYPOINT ----------------
ENTRYPOINT ["/entrypoint.sh"]
