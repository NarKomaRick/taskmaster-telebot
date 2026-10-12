FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
ARG BASE_PATH=/taskmaster
ENV BASE_PATH=$BASE_PATH
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home appuser && mkdir -p /data/attachments && chown -R appuser:appuser /data
COPY --chown=appuser:appuser backend .
COPY --from=frontend /build/dist /app/static
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
