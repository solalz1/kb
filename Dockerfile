# --- 1. Front (PWA) -----------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- 2. API + worker + serveur MCP ---------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STATIC_DIR=/app/static

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates postgresql-client \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp a besoin d'un runtime JavaScript pour YouTube (Deno recommandé)
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
COPY supabase/ ./supabase/
COPY --from=web /web/dist /app/static

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
