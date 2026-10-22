# Single image: builds both web apps, then serves the chosen one (WEB_APP=classic or next) and the API from FastAPI.
FROM node:25-alpine AS web
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

FROM node:25-alpine AS web-next
WORKDIR /app/apps/web-next
COPY apps/web-next/package.json apps/web-next/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web-next/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/apps/api
COPY apps/api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/api/ ./
COPY --from=web /app/apps/web/dist /app/apps/web/dist
COPY --from=web-next /app/apps/web-next/dist /app/apps/web-next/dist
# Runs as a user without rights to the image's files.
RUN useradd --system --no-create-home app
USER app
EXPOSE 8000
# On every start: bring the app's own tables up to date, load the sample data if it isn't there yet (unless
# SAMPLE_DATA=false), then serve.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed --if-empty && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
