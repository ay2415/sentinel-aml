# Build context = repository root:  docker build -f backend/Dockerfile .
# ---- build stage: compile wheels once; the toolchain never reaches the runtime image
FROM python:3.12-slim AS build
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---- runtime stage: slim, non-root, read-only friendly
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /srv app
WORKDIR /srv/backend
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY backend/app ./app
COPY prompts /srv/prompts
COPY data/knowledge_base /srv/data/knowledge_base
RUN mkdir -p /srv/models /srv/data/raw /srv/evaluation/reports && chown -R app:app /srv
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/health/live || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
