# One image, one URL: FastAPI serves the API and the built React app.
# For any container host (uses $PORT when set, 7860 otherwise). Note: Hugging Face Spaces only allow outbound
# traffic on ports 80/443/8080, so they cannot reach Postgres (5432) or Neo4j Bolt (7687); use another host.

# ---- 1. build the frontend -------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# empty VITE_API_URL => the UI calls the API on its own origin
RUN VITE_API_URL="" npm run build

# ---- 2. python runtime -----------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/app/.fastembed HF_HOME=/app/.hf \
    GRAPHITI_TELEMETRY_ENABLED=false RESEARCH_CACHE_TTL_HOURS=0
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /web/dist backend/frontend_dist/
# bake the embedding model into the image so the first request is not a download
RUN cd backend && python -c "from app.embeddings import embed_one; print(len(embed_one('warm-up')))"
# Spaces run as a non-root user: make runtime dirs writable
RUN mkdir -p backend/data backend/.cache && chmod -R 777 backend/data backend/.cache /app/.fastembed /app/.hf 2>/dev/null || true
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.api:app --app-dir backend --host 0.0.0.0 --port ${PORT:-7860}"]
