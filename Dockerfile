FROM node:22-bookworm-slim AS frontend
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements-tested.txt ./
RUN pip install --no-cache-dir -r requirements-tested.txt
COPY backend ./backend
COPY --from=frontend /ui/dist ./frontend/dist
COPY run.py pyproject.toml ./
RUN pip install --no-cache-dir --no-deps .
CMD ["python", "run.py"]
